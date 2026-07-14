from dataclasses import dataclass

from .models import BotEvent


@dataclass(frozen=True)
class CrawlerMatch:
    token: str
    provider: str
    crawler: str
    category: str


def _rule(token, provider, category, crawler=None):
    return CrawlerMatch(
        token=token.lower(),
        provider=provider,
        crawler=crawler or token,
        category=category,
    )


# Keep this catalog explicit and reviewable. Specific/long tokens are checked before
# generic ones so Applebot-Extended, Grok-DeepSearch, and similar names are not folded
# into their broader crawler families.
CRAWLER_RULES = tuple(
    sorted(
        (
            # User-triggered AI answer fetchers.
            _rule("ChatGPT-User", "OpenAI", BotEvent.Category.ANSWER),
            _rule("Claude-User", "Anthropic", BotEvent.Category.ANSWER),
            _rule("Perplexity-User", "Perplexity", BotEvent.Category.ANSWER),
            _rule("Google-Agent", "Google", BotEvent.Category.ANSWER),
            _rule("Google-NotebookLM", "Google", BotEvent.Category.ANSWER),
            _rule("Google-Read-Aloud", "Google", BotEvent.Category.ANSWER),
            _rule("GoogleAgent", "Google", BotEvent.Category.ANSWER),
            _rule("MistralAI-User", "Mistral", BotEvent.Category.ANSWER),
            _rule("Copilot", "Microsoft", BotEvent.Category.ANSWER),
            _rule("Amzn-User", "Amazon", BotEvent.Category.ANSWER),
            _rule("DuckAssistBot", "DuckDuckGo", BotEvent.Category.ANSWER),
            _rule("xAI-SearchBot", "xAI", BotEvent.Category.ANSWER),
            _rule("Grok-DeepSearch", "xAI", BotEvent.Category.ANSWER),
            _rule("meta-externalfetcher", "Meta", BotEvent.Category.ANSWER),
            _rule("Kimi-User", "Moonshot AI", BotEvent.Category.ANSWER),
            _rule("Qwen-User", "Alibaba", BotEvent.Category.ANSWER),
            # Search and answer-index crawlers.
            _rule("OAI-SearchBot", "OpenAI", BotEvent.Category.INDEXING),
            _rule("Claude-SearchBot", "Anthropic", BotEvent.Category.INDEXING),
            _rule("PerplexityBot", "Perplexity", BotEvent.Category.INDEXING),
            _rule("Google-InspectionTool", "Google", BotEvent.Category.INDEXING),
            _rule("Googlebot", "Google", BotEvent.Category.INDEXING),
            _rule("MistralAI-Index", "Mistral", BotEvent.Category.INDEXING),
            _rule("bingbot", "Microsoft", BotEvent.Category.INDEXING),
            _rule("msnbot", "Microsoft", BotEvent.Category.INDEXING),
            _rule("Amzn-SearchBot", "Amazon", BotEvent.Category.INDEXING),
            _rule("meta-webindexer", "Meta", BotEvent.Category.INDEXING),
            _rule("Kimi-SearchBot", "Moonshot AI", BotEvent.Category.INDEXING),
            _rule("TikTokSpider", "ByteDance", BotEvent.Category.INDEXING),
            _rule("Baiduspider", "Baidu", BotEvent.Category.INDEXING),
            _rule("YouBot", "You.com", BotEvent.Category.INDEXING),
            # Public-content and model-training crawlers.
            _rule("GPTBot", "OpenAI", BotEvent.Category.TRAINING),
            _rule("ClaudeBot", "Anthropic", BotEvent.Category.TRAINING),
            _rule("GoogleOther", "Google", BotEvent.Category.TRAINING),
            _rule("Google-CloudVertexBot", "Google", BotEvent.Category.TRAINING),
            _rule("Applebot-Extended", "Apple", BotEvent.Category.TRAINING),
            _rule("Applebot", "Apple", BotEvent.Category.TRAINING),
            _rule("Amazonbot", "Amazon", BotEvent.Category.TRAINING),
            _rule("meta-externalagent", "Meta", BotEvent.Category.TRAINING),
            _rule("KimiBot", "Moonshot AI", BotEvent.Category.TRAINING),
            _rule("Bytespider", "ByteDance", BotEvent.Category.TRAINING),
            _rule("ERNIEBot", "Baidu", BotEvent.Category.TRAINING),
            _rule("QwenBot", "Alibaba", BotEvent.Category.TRAINING),
            _rule("ChatGLM-Spider", "Zhipu AI", BotEvent.Category.TRAINING),
            _rule("DeepSeekBot", "DeepSeek", BotEvent.Category.TRAINING),
            _rule("cohere-ai", "Cohere", BotEvent.Category.TRAINING),
            _rule(
                "cohere-training-data-crawler",
                "Cohere",
                BotEvent.Category.TRAINING,
            ),
            _rule("AI2Bot", "Allen AI", BotEvent.Category.TRAINING),
            _rule("CCBot", "Common Crawl", BotEvent.Category.TRAINING),
            # Other known AI and social crawler traffic.
            _rule("OAI-AdsBot", "OpenAI", BotEvent.Category.OTHER),
            _rule("GrokBot", "xAI", BotEvent.Category.OTHER),
            _rule("xAI-Bot", "xAI", BotEvent.Category.OTHER),
            _rule("xAI-Grok", "xAI", BotEvent.Category.OTHER),
            _rule("xAI-Web-Crawler", "xAI", BotEvent.Category.OTHER),
            _rule("Grok", "xAI", BotEvent.Category.OTHER),
            _rule("meta-externalads", "Meta", BotEvent.Category.OTHER),
            _rule("facebookexternalhit", "Meta", BotEvent.Category.OTHER),
            _rule("FacebookBot", "Meta", BotEvent.Category.OTHER),
            _rule("Doubaobot", "ByteDance", BotEvent.Category.OTHER),
            _rule("YiyanBot", "Baidu", BotEvent.Category.OTHER),
            _rule("TongyiBot", "Alibaba", BotEvent.Category.OTHER),
            _rule("AliyunBot", "Alibaba", BotEvent.Category.OTHER),
        ),
        key=lambda rule: len(rule.token),
        reverse=True,
    )
)


def classify_crawler(user_agent):
    normalized = (user_agent or "").lower()
    if not normalized:
        return None
    return next((rule for rule in CRAWLER_RULES if rule.token in normalized), None)
