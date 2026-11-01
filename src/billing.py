"""Pay-per-event names. Prices are set in Apify Console, not here."""
import monitor as M

PAGE_CHECKED = "page-checked"
ANOMALY_DETECTED = "anomaly-detected"

EVENTS = {
    PAGE_CHECKED: "A URL was fetched and its page classified (any status, including OK).",
    ANOMALY_DETECTED: "A checked page was reported as SCHEMA_DRIFT, SOFT_BLOCK or PARTIAL.",
}
ANOMALY_STATUSES = (M.DRIFT, M.SOFT_BLOCK, M.PARTIAL)

def events_for(item):
    """Events one dataset item is worth. A page we never got (robots, network, HTTP error)
    was not checked, so it is not charged."""
    if not item.get("checked"):
        return []
    events = [PAGE_CHECKED]
    if item.get("status") in ANOMALY_STATUSES:
        events.append(ANOMALY_DETECTED)
    return events
