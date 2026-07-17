from dataclasses import dataclass


EXPLICIT_AUTOMATION_SCORE_THRESHOLD = 80
HIGH_VOLUME_PAGEVIEW_THRESHOLD = 50
RAPID_BURST_PAGEVIEW_THRESHOLD = 20
RAPID_BURST_WINDOW_SECONDS = 10 * 60
SESSION_CHURN_THRESHOLD = 20

AUTOMATION_REASON_LABELS = {
    "webdriver": "Browser automation signal",
    "headless_user_agent": "Headless browser user-agent",
    "high_request_volume": "High request volume",
    "rapid_navigation_burst": "Rapid navigation burst",
    "session_churn": "Repeated session churn",
}

HEADLESS_USER_AGENT_TOKENS = (
    "headlesschrome",
    "phantomjs",
    "slimerjs",
    "playwright",
    "puppeteer",
    "selenium",
    "cypress",
)


@dataclass(frozen=True)
class AutomationAssessment:
    score: int
    reasons: tuple[str, ...]


def assess_browser_automation(user_agent, signals):
    reasons = []
    if getattr(signals, "webdriver", False):
        reasons.append("webdriver")

    normalized_user_agent = (user_agent or "").lower()
    if any(token in normalized_user_agent for token in HEADLESS_USER_AGENT_TOKENS):
        reasons.append("headless_user_agent")

    return AutomationAssessment(
        score=100 if reasons else 0,
        reasons=tuple(dict.fromkeys(reasons)),
    )
