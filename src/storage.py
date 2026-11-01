"""Where input comes from and results go. Locally that is the Apify local storage layout on
disk; on the Apify platform it is the Apify SDK. The SDK is not a dependency of this repo, so
it is imported only when a platform run actually opens it."""
import asyncio, json, os

def is_platform(env=None):
    env = os.environ if env is None else env
    return env.get("APIFY_IS_AT_HOME") == "1"

class LocalStorage:
    """storage/key_value_stores/default/<KEY>.json and storage/datasets/default/<n>.json."""

    def __init__(self, root=None, env=None):
        env = os.environ if env is None else env
        self.root = root or env.get("APIFY_LOCAL_STORAGE_DIR") or env.get("CRAWLEE_STORAGE_DIR") or "storage"
        self.kv_dir = os.path.join(self.root, "key_value_stores", "default")
        self.ds_dir = os.path.join(self.root, "datasets", "default")
        self.charges = []

    def open(self):
        os.makedirs(self.kv_dir, exist_ok=True)
        os.makedirs(self.ds_dir, exist_ok=True)

    def close(self):
        pass

    def fail(self, message):
        pass

    def get_input(self):
        return self.get_value("INPUT", {}) or {}

    def get_value(self, key, default=None):
        path = os.path.join(self.kv_dir, f"{key}.json")
        if not os.path.exists(path):
            return default
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def set_value(self, key, value):
        os.makedirs(self.kv_dir, exist_ok=True)
        path = os.path.join(self.kv_dir, f"{key}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def push_data(self, item):
        os.makedirs(self.ds_dir, exist_ok=True)
        taken = [int(f[:-5]) for f in os.listdir(self.ds_dir) if f.endswith(".json") and f[:-5].isdigit()]
        path = os.path.join(self.ds_dir, f"{max(taken, default=0) + 1:09d}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(item, fh, ensure_ascii=False, indent=2)

    def charge(self, event, count=1):
        # Nothing is billed locally; the record is there so a dry run shows what would be.
        self.charges.append((event, count))

class ApifyStorage:
    """Thin sync wrapper over the async Apify SDK `Actor`. Pass `actor` to test without the SDK."""

    def __init__(self, actor=None):
        self.actor = actor
        self._loop = None

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def open(self):
        if self.actor is None:
            from apify import Actor  # deferred: only present inside the Actor image
            self.actor = Actor
        self._loop = asyncio.new_event_loop()
        self._run(self.actor.init())

    def close(self):
        try:
            self._run(self.actor.exit())
        finally:
            self._loop.close()

    def fail(self, message):
        try:
            self._run(self.actor.fail(exit_code=2, status_message=message))
        finally:
            self._loop.close()

    def get_input(self):
        return self._run(self.actor.get_input()) or {}

    def get_value(self, key, default=None):
        v = self._run(self.actor.get_value(key))
        return default if v is None else v

    def set_value(self, key, value):
        self._run(self.actor.set_value(key, value))

    def push_data(self, item):
        self._run(self.actor.push_data(item))

    def charge(self, event, count=1):
        self._run(self.actor.charge(event_name=event, count=count))

def open_storage(env=None):
    return ApifyStorage() if is_platform(env) else LocalStorage(env=env)
