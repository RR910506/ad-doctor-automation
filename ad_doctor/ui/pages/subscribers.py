from ui.components import empty_state, page_header
from ui.pages.shared import render_directory


def render(st, service, actions):
    if not service.capabilities.get("subscribers_available", service.demo):
        page_header(st, "Subscribers", "Manage consent and delivery eligibility with masked subscriber identities.")
        empty_state(st, "Subscriber delivery is not activated", "No subscriber architecture is available in this workspace. Notification recipients are not treated as subscribers.", "Activate the subscriber architecture in a later controlled phase.")
        return
    render_directory(st, service, "subscribers", "Subscribers", "Manage consent and delivery eligibility with masked subscriber identities.", ("active", "paused", "unsubscribed", "bounced", "suppressed"))
