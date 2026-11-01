"""The Actor packaging. No test here touches the network: every request goes through
FakeTransport, and the Apify SDK is replaced by a mock."""
import asyncio, json, os, sys, tempfile, unittest, urllib.error
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import monitor as M
import actor as A
import billing
import storage as S
import transport as T

ROOT = os.path.join(os.path.dirname(__file__), "..")
SNAPS = os.path.join(ROOT, "fixtures", "snapshots")
DAYS = sorted(f[:-5] for f in os.listdir(SNAPS) if f.endswith(".html"))
URL = "https://shop.example/listing"
ROBOTS_OK = T.Response(200, "User-agent: *\nDisallow: /private/\n")

def snapshot(day):
    with open(os.path.join(SNAPS, f"{day}.html"), encoding="utf-8") as fh:
        return fh.read()

def fetcher(routes, **kw):
    clock = T.FakeClock()
    tr = T.FakeTransport(routes, clock=clock)
    kw.setdefault("interval", 0)
    return T.Fetcher(tr, "test-agent/1.0", clock=clock, sleep=clock.sleep, **kw), tr, clock

class TheBaselineRuleSurvivesBeingPersisted(unittest.TestCase):
    """The Actor sees one day per run and stores the baseline in between. Round-tripping the
    state through JSON every day must give exactly the snapshot replay's verdicts."""

    def test_json_round_trip_each_day_matches_the_replay(self):
        replay = M.run(SNAPS)
        saved, stepped = None, []
        for d in DAYS:
            state = M.Baseline.from_dict(json.loads(saved) if saved else None)
            stepped.append(M.step(state, d, snapshot(d)))
            saved = json.dumps(state.to_dict())
        self.assertEqual([(r.status, r.note, r.changes) for r in stepped],
                         [(r.status, r.note, r.changes) for r in replay])

class FetcherManners(unittest.TestCase):
    def test_robots_disallow_is_respected_and_the_page_is_never_requested(self):
        f, tr, _ = fetcher({"https://a.example/robots.txt": T.Response(200, "User-agent: *\nDisallow: /\n")})
        got = f.fetch("https://a.example/page")
        self.assertTrue(got.robots_disallowed)
        self.assertEqual([c["url"] for c in tr.calls], ["https://a.example/robots.txt"])

    def test_robots_rules_are_matched_against_our_user_agent(self):
        f, _, _ = fetcher({"https://a.example/robots.txt": T.Response(200, "User-agent: test-agent\nDisallow: /\n"),
                           "https://a.example/p": T.Response(200, "x")})
        self.assertTrue(f.fetch("https://a.example/p").robots_disallowed)

    def test_missing_robots_means_allowed_and_is_fetched_once_per_host(self):
        f, tr, _ = fetcher({"https://a.example/1": T.Response(200, "x"), "https://a.example/2": T.Response(200, "y")})
        self.assertTrue(f.fetch("https://a.example/1").ok)
        self.assertTrue(f.fetch("https://a.example/2").ok)
        self.assertEqual(sum(c["url"].endswith("/robots.txt") for c in tr.calls), 1)

    def test_unreadable_robots_is_not_taken_as_permission(self):
        f, tr, _ = fetcher({"https://a.example/robots.txt": T.TransportError("refused"),
                            "https://a.example/p": T.Response(200, "x")}, retries=1)
        got = f.fetch("https://a.example/p")
        self.assertTrue(got.robots_disallowed)
        self.assertIn("unavailable", got.error)
        self.assertNotIn("https://a.example/p", [c["url"] for c in tr.calls])

    def test_robots_403_disallows_everything(self):
        f, _, _ = fetcher({"https://a.example/robots.txt": T.Response(403, "")})
        self.assertTrue(f.fetch("https://a.example/p").robots_disallowed)

    def test_retries_with_exponential_backoff_then_succeeds(self):
        f, tr, clock = fetcher({"https://a.example/robots.txt": T.Response(404, ""),
                                "https://a.example/p": [T.Response(503, ""), T.TransportError("reset"),
                                                        T.Response(429, ""), T.Response(200, "ok")]},
                               retries=3, backoff=1.0, respect_robots=True)
        got = f.fetch("https://a.example/p")
        self.assertTrue(got.ok)
        self.assertEqual(got.attempts, 4)
        self.assertEqual(clock.sleeps, [1.0, 2.0, 4.0])

    def test_retry_after_is_honoured_up_to_the_cap(self):
        f, _, clock = fetcher({"https://a.example/p": [T.Response(429, "", headers={"Retry-After": "7"}),
                                                       T.Response(200, "ok")]},
                              respect_robots=False, backoff=1.0, max_backoff=60)
        self.assertTrue(f.fetch("https://a.example/p").ok)
        self.assertEqual(clock.sleeps, [7.0])

    def test_gives_up_after_the_retry_budget_and_says_why(self):
        f, tr, _ = fetcher({"https://a.example/p": T.Response(503, "")}, respect_robots=False, retries=2)
        got = f.fetch("https://a.example/p")
        self.assertFalse(got.ok)
        self.assertEqual(got.error, "HTTP 503")
        self.assertEqual(len(tr.calls), 3)

    def test_client_errors_are_not_retried(self):
        f, tr, _ = fetcher({"https://a.example/p": T.Response(404, "")}, respect_robots=False)
        got = f.fetch("https://a.example/p")
        self.assertEqual((got.ok, got.error, len(tr.calls)), (False, "HTTP 404", 1))

    def test_requests_to_one_domain_are_spaced_but_other_domains_are_not_held_up(self):
        routes = {u: T.Response(200, "x") for u in
                  ("https://a.example/1", "https://a.example/2", "https://b.example/1", "https://a.example/3")}
        f, tr, _ = fetcher(routes, interval=2.5, respect_robots=False)
        for u in routes:
            f.fetch(u)
        at = {c["url"]: c["at"] for c in tr.calls}
        self.assertEqual(at["https://a.example/1"], 0)
        self.assertEqual(at["https://a.example/2"], 2.5)
        self.assertEqual(at["https://b.example/1"], 2.5)  # no wait of its own
        self.assertEqual(at["https://a.example/3"], 5.0)

    def test_timeout_and_user_agent_reach_the_transport(self):
        f, tr, _ = fetcher({"https://a.example/p": T.Response(200, "x")}, timeout=12.0, respect_robots=False)
        f.fetch("https://a.example/p")
        self.assertEqual(tr.calls[0]["timeout"], 12.0)
        self.assertEqual(tr.calls[0]["headers"]["User-Agent"], "test-agent/1.0")

class UrllibTransportMapsErrors(unittest.TestCase):
    """urlopen is patched; these check the mapping, not a real connection."""

    def test_network_error_becomes_transport_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
            with self.assertRaises(T.TransportError):
                T.UrllibTransport().get("https://a.example/", {}, 1)

    def test_timeout_becomes_transport_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(T.TransportError):
                T.UrllibTransport().get("https://a.example/", {}, 1)

    def test_http_error_is_a_response_not_an_exception(self):
        import email.message, io
        hdrs = email.message.Message()
        err = urllib.error.HTTPError("https://a.example/", 503, "busy", hdrs, io.BytesIO(b"later"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            r = T.UrllibTransport().get("https://a.example/", {}, 1)
        self.assertEqual((r.status, r.text), (503, "later"))

    def test_timeout_is_passed_to_urlopen(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")) as uo:
            with self.assertRaises(T.TransportError):
                T.UrllibTransport().get("https://a.example/", {"User-Agent": "u"}, 9)
        self.assertEqual(uo.call_args.kwargs["timeout"], 9)

class LocalStorageLayout(unittest.TestCase):
    def test_reads_input_and_writes_numbered_dataset_items(self):
        with tempfile.TemporaryDirectory() as td:
            st = S.LocalStorage(root=td)
            st.open()
            with open(os.path.join(td, "key_value_stores", "default", "INPUT.json"), "w") as fh:
                json.dump({"startUrls": [URL]}, fh)
            self.assertEqual(st.get_input(), {"startUrls": [URL]})
            st.push_data({"n": 1})
            st.push_data({"n": 2})
            ds = os.path.join(td, "datasets", "default")
            self.assertEqual(sorted(os.listdir(ds)), ["000000001.json", "000000002.json"])
            with open(os.path.join(ds, "000000002.json")) as fh:
                self.assertEqual(json.load(fh), {"n": 2})
            st.set_value("K", {"a": 1})
            self.assertEqual(st.get_value("K"), {"a": 1})
            self.assertEqual(st.get_value("missing", "d"), "d")

    def test_storage_dir_comes_from_the_apify_env_var(self):
        st = S.LocalStorage(env={"APIFY_LOCAL_STORAGE_DIR": "/x/y"})
        self.assertEqual(st.ds_dir, os.path.join("/x/y", "datasets", "default"))

class PlatformAdapter(unittest.TestCase):
    def test_platform_is_detected_from_the_environment(self):
        self.assertTrue(S.is_platform({"APIFY_IS_AT_HOME": "1"}))
        self.assertFalse(S.is_platform({}))
        self.assertIsInstance(S.open_storage({"APIFY_IS_AT_HOME": "1"}), S.ApifyStorage)
        self.assertIsInstance(S.open_storage({}), S.LocalStorage)

    def test_the_sdk_is_not_imported_until_a_platform_run_opens_it(self):
        self.assertNotIn("apify", sys.modules)
        S.ApifyStorage()  # constructing is not opening
        self.assertNotIn("apify", sys.modules)

    def test_open_imports_the_sdk_lazily(self):
        fake = mock.MagicMock()
        fake.Actor = mock.AsyncMock()
        with mock.patch.dict(sys.modules, {"apify": fake}):
            st = S.ApifyStorage()
            st.open()
            st.close()
        fake.Actor.init.assert_awaited_once()
        fake.Actor.exit.assert_awaited_once()

    def test_calls_are_forwarded_to_the_sdk(self):
        sdk = mock.AsyncMock()
        sdk.get_input.return_value = {"startUrls": [URL]}
        sdk.get_value.return_value = None
        st = S.ApifyStorage(actor=sdk)
        st.open()
        self.assertEqual(st.get_input(), {"startUrls": [URL]})
        self.assertEqual(st.get_value("K", {}), {})
        st.set_value("K", {"a": 1})
        st.push_data({"n": 1})
        st.charge("page-checked", 2)
        st.close()
        sdk.set_value.assert_awaited_once_with("K", {"a": 1})
        sdk.push_data.assert_awaited_once_with({"n": 1})
        sdk.charge.assert_awaited_once_with(event_name="page-checked", count=2)

class InputParsing(unittest.TestCase):
    def test_accepts_request_list_objects_and_strings_and_dedupes(self):
        cfg = A.parse_input({"startUrls": [{"url": URL}, URL, "https://b.example/"]})
        self.assertEqual(cfg["urls"], [URL, "https://b.example/"])
        self.assertIs(cfg["selectors"], M.SELECTORS)

    def test_max_pages_caps_the_run(self):
        urls = [f"https://a.example/{i}" for i in range(5)]
        cfg = A.parse_input({"startUrls": urls, "maxPages": 2})
        self.assertEqual(cfg["urls"], urls[:2])
        self.assertEqual(cfg["skipped"], urls[2:])

    def test_custom_selectors_become_generations(self):
        cfg = A.parse_input({"startUrls": [URL], "selectors": [
            {"name": "v1", "row": "<tr>(.*?)</tr>", "key": "<td>(.*?)</td>", "value": "<b>(.*?)</b>"}]})
        self.assertEqual(cfg["selectors"], [{"name": "v1", "row": "<tr>(.*?)</tr>",
                                              "f1": "<td>(.*?)</td>", "f2": "<b>(.*?)</b>"}])

    def test_bad_input_is_rejected_with_a_reason(self):
        for raw, msg in (({}, "empty"), ({"startUrls": ["ftp://x"]}, "http"),
                         ({"startUrls": [URL], "selectors": [{"row": "(", "key": "a", "value": "b"}]}, "regex"),
                         ({"startUrls": [URL], "selectors": [{"row": "a"}]}, "missing"),
                         ({"startUrls": [URL], "maxPages": 0}, "maxPages")):
            with self.assertRaises(A.InputError) as cm:
                A.parse_input(raw)
            self.assertIn(msg, str(cm.exception))

    def test_schema_defaults_match_the_code(self):
        with open(os.path.join(ROOT, ".actor", "input_schema.json")) as fh:
            schema = json.load(fh)
        props = schema["properties"]
        for k in ("startUrls", "selectors", "maxPages", "requestIntervalMillis", "userAgent"):
            self.assertIn(k, props)
        for k, v in A.DEFAULTS.items():
            self.assertEqual(props[k]["default"], v, k)
        self.assertEqual(schema["required"], ["startUrls"])
        prefill = A.parse_input({"startUrls": [URL], "selectors": props["selectors"]["prefill"]})["selectors"]
        self.assertEqual(prefill, M.SELECTORS)

class ActorFiles(unittest.TestCase):
    def test_actor_json_points_at_files_that_exist(self):
        with open(os.path.join(ROOT, ".actor", "actor.json")) as fh:
            spec = json.load(fh)
        self.assertEqual(spec["actorSpecification"], 1)
        for k in ("input", "dockerfile"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, ".actor", spec[k])), k)

    def test_dockerfile_uses_the_apify_python_image_and_runs_the_actor(self):
        with open(os.path.join(ROOT, "Dockerfile")) as fh:
            text = fh.read()
        self.assertIn("FROM apify/actor-python:", text)
        self.assertIn("src/actor.py", text)

def run_day(td, routes, day, input_=None, transport=None):
    st = S.LocalStorage(root=td)
    st.open()
    st.set_value("INPUT", input_ or {"startUrls": [URL], "requestIntervalMillis": 0})
    clock = T.FakeClock()
    tr = transport or T.FakeTransport(routes, clock=clock)
    items = A.run_actor(st, tr, clock=clock, sleep=clock.sleep, now=day, log=lambda *_: None)
    return items, st

class TheActorOverSevenScheduledRuns(unittest.TestCase):
    """The seven fixture days served as seven separate Actor runs, state on disk in between."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.items, cls.charges = {}, []
        for d in DAYS:
            items, st = run_day(cls.td.name, {URL: T.Response(200, snapshot(d)),
                                              "https://shop.example/robots.txt": ROBOTS_OK}, d)
            cls.items[d] = items[0]
            cls.charges += st.charges

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_the_actor_reports_the_same_failures_as_the_replay(self):
        self.assertEqual({d: i["status"] for d, i in self.items.items()},
                         {r.day: r.status for r in M.run(SNAPS)})
        self.assertEqual(self.items["2026-03-03"]["status"], M.DRIFT)
        self.assertEqual(self.items["2026-03-05"]["status"], M.SOFT_BLOCK)
        self.assertEqual(self.items["2026-03-06"]["status"], M.PARTIAL)

    def test_the_real_price_move_survives_two_broken_runs(self):
        cyan = [c for c in self.items["2026-03-07"]["changes"] if c["key"] == "Cyan"]
        self.assertEqual(cyan, [{"key": "Cyan", "before": "75.50", "after": "755.00", "ratio": 10.0}])
        self.assertFalse([c for c in self.items["2026-03-07"]["changes"] if c["key"] in ("Bravo", "Delta")])

    def test_every_run_is_one_dataset_item(self):
        ds = os.path.join(self.td.name, "datasets", "default")
        self.assertEqual(len(os.listdir(ds)), len(DAYS))
        with open(os.path.join(ds, "000000006.json")) as fh:
            item = json.load(fh)
        self.assertEqual((item["status"], item["rowCount"], item["checked"]), (M.PARTIAL, 1, True))

    def test_charges_one_page_checked_per_run_and_one_anomaly_per_flagged_run(self):
        self.assertEqual(self.charges.count((billing.PAGE_CHECKED, 1)), 7)
        self.assertEqual(self.charges.count((billing.ANOMALY_DETECTED, 1)), 3)

class ARunThatNeverSawThePage(unittest.TestCase):
    def _seed(self, td):
        for d in DAYS[:2]:
            run_day(td, {URL: T.Response(200, snapshot(d))}, d)
        return S.LocalStorage(root=td).get_value(A.STATE_KEY)

    def test_fetch_failure_is_reported_not_charged_and_not_a_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            before = self._seed(td)
            items, st = run_day(td, {URL: T.Response(503, "")}, "2026-03-03")
            self.assertEqual(items[0]["status"], A.FETCH_FAILED)
            self.assertEqual(items[0]["httpStatus"], 503)
            self.assertEqual(st.charges, [])
            self.assertEqual(S.LocalStorage(root=td).get_value(A.STATE_KEY), before)

    def test_robots_disallow_is_reported_not_charged_and_not_a_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            before = self._seed(td)
            items, st = run_day(td, {"https://shop.example/robots.txt": T.Response(200, "User-agent: *\nDisallow: /\n"),
                                     URL: T.Response(200, snapshot("2026-03-04"))}, "2026-03-03")
            self.assertEqual(items[0]["status"], A.ROBOTS_DISALLOWED)
            self.assertEqual(st.charges, [])
            self.assertEqual(S.LocalStorage(root=td).get_value(A.STATE_KEY), before)

    def test_a_partial_run_does_not_become_the_baseline_of_the_next(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed(td)
            run_day(td, {URL: T.Response(200, snapshot("2026-03-06"))}, "2026-03-03")
            items, _ = run_day(td, {URL: T.Response(200, snapshot("2026-03-07"))}, "2026-03-04")
            day2 = M.parse(snapshot(DAYS[1]))[0]
            day7 = M.parse(snapshot("2026-03-07"))[0]
            self.assertEqual([(c["key"], c["before"], c["after"]) for c in items[0]["changes"]],
                             [c[:3] for c in M.diff(day2, day7)])

class ChargingOnThePlatform(unittest.TestCase):
    def test_run_actor_charges_through_the_sdk(self):
        sdk = mock.AsyncMock()
        sdk.get_input.return_value = {"startUrls": [URL], "requestIntervalMillis": 0}
        sdk.get_value.return_value = None
        clock = T.FakeClock()
        tr = T.FakeTransport({URL: T.Response(200, snapshot("2026-03-05"))}, clock=clock)
        A.run_actor(S.ApifyStorage(actor=sdk), tr, clock=clock, sleep=clock.sleep, now="d", log=lambda *_: None)
        self.assertEqual(sdk.charge.await_args_list,
                         [mock.call(event_name="page-checked", count=1),
                          mock.call(event_name="anomaly-detected", count=1)])
        sdk.exit.assert_awaited_once()

    def test_bad_input_fails_the_run_through_the_sdk(self):
        sdk = mock.AsyncMock()
        sdk.get_input.return_value = {}
        with self.assertRaises(A.InputError):
            A.run_actor(S.ApifyStorage(actor=sdk), T.FakeTransport({}), log=lambda *_: None)
        sdk.fail.assert_awaited_once()
        sdk.charge.assert_not_awaited()

    def test_events_are_only_the_two_defined(self):
        self.assertEqual(set(billing.EVENTS), {"page-checked", "anomaly-detected"})
        self.assertEqual(billing.events_for({"checked": True, "status": M.OK}), ["page-checked"])
        self.assertEqual(billing.events_for({"checked": True, "status": M.EMPTY_SOURCE}), ["page-checked"])
        self.assertEqual(billing.events_for({"checked": False, "status": A.FETCH_FAILED}), [])

if __name__ == "__main__":
    unittest.main()
