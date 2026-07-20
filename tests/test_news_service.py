"""
Unit tests for services/news_service.py
Tests sentiment analysis and cache management.
"""
import sys
import os
import time

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.news_service import (
    NewsService,
    POSITIVE_KEYWORDS,
    NEGATIVE_KEYWORDS,
    news_service,
)


def make_article(headline, summary=""):
    """Helper to create article dict."""
    return {
        'headline': headline,
        'summary': summary,
        'source': 'Test Source',
        'datetime': '2026-07-20T10:00:00Z',
        'url': 'https://example.com'
    }


# === Sentiment Analysis Tests ===

class TestSentimentAnalysis:
    """Tests for sentiment analysis function."""

    def test_empty_articles_neutral(self):
        """No articles should return neutral sentiment."""
        result = NewsService().analyze_sentiment([])
        assert result['overall'] == 'neutral'
        assert result['score'] == 0
        assert result['headline_count'] == 0
        assert 'Tidak ada berita' in result['summary']

    def test_positive_keywords_score(self):
        """Articles with positive keywords should score positive."""
        articles = [
            make_article("Stock surge on strong profit growth"),
            make_article("Shares rally on positive outlook")
        ]
        result = NewsService().analyze_sentiment(articles)
        assert result['overall'] == 'positive'
        assert result['positive_count'] >= 2
        assert result['negative_count'] == 0

    def test_negative_keywords_score(self):
        """Articles with negative keywords should score negative."""
        articles = [
            make_article("Stock crash on heavy loss"),
            make_article("Shares plunge amid fear and concern")
        ]
        result = NewsService().analyze_sentiment(articles)
        assert result['overall'] == 'negative'
        assert result['negative_count'] >= 2
        assert result['positive_count'] == 0

    def test_mixed_sentiment_neutral(self):
        """Mixed articles should be neutral."""
        articles = [
            make_article("Stock surge on profit"),
            make_article("Stock fall on loss")
        ]
        result = NewsService().analyze_sentiment(articles)
        # Should be roughly neutral with equal counts
        assert result['overall'] == 'neutral'

    def test_sentiment_emoji_assignment(self):
        """Sentiment emoji should match overall."""
        positive_articles = [make_article("Strong growth surge gain")]
        negative_articles = [make_article("Heavy loss crash fall")]
        neutral_articles = [make_article("Regular news about company")]

        positive = NewsService().analyze_sentiment(positive_articles)
        negative = NewsService().analyze_sentiment(negative_articles)
        neutral = NewsService().analyze_sentiment(neutral_articles)

        assert positive['emoji'] == '🟢'
        assert negative['emoji'] == '🔴'
        assert neutral['emoji'] == '🟡'

    def test_sentiment_includes_all_headlines(self):
        """all_headlines should contain all analyzed articles."""
        articles = [
            make_article("First headline"),
            make_article("Second headline"),
            make_article("Third headline")
        ]
        result = NewsService().analyze_sentiment(articles)
        assert 'all_headlines' in result
        assert len(result['all_headlines']) == 3

    def test_sentiment_top_headlines(self):
        """top_headlines should contain headlines with strong score."""
        articles = [
            make_article("Stock surge profit growth rise up"),  # Many positive
            make_article("Regular news")  # Neutral
        ]
        result = NewsService().analyze_sentiment(articles)
        # First article should be in top_headlines (score >= 2)
        assert len(result['top_headlines']) >= 1

    def test_sentiment_max_articles(self):
        """Should analyze at most 10 articles."""
        articles = [make_article(f"Article {i} positive gain") for i in range(15)]
        result = NewsService().analyze_sentiment(articles)
        # all_headlines has 5 max
        assert len(result['all_headlines']) <= 5


# === Sentiment Edge Cases ===

class TestSentimentEdgeCases:
    """Edge case tests for sentiment analysis."""

    def test_article_with_no_headline(self):
        """Article without headline should not crash."""
        articles = [{'summary': 'just a summary', 'source': 'Test'}]
        result = NewsService().analyze_sentiment(articles)
        assert result is not None

    def test_article_with_special_chars(self):
        """Special characters in headline should not crash."""
        articles = [make_article("Stock ↑ gained +5%! 🎉 surge")]
        result = NewsService().analyze_sentiment(articles)
        assert result is not None
        assert result['positive_count'] >= 1

    def test_very_long_headline(self):
        """Very long headlines should not crash."""
        long_headline = "surge " * 1000
        articles = [make_article(long_headline)]
        result = NewsService().analyze_sentiment(articles)
        assert result is not None


# === Cache Tests ===

class TestNewsCache:
    """Tests for news service cache."""

    def test_cache_initialization(self):
        """Service should initialize with empty cache."""
        service = NewsService()
        assert isinstance(service.cache, dict)
        assert service.cache_ttl > 0

    def test_cache_ttl_one_hour(self):
        """Cache TTL should be 1 hour (3600 seconds)."""
        service = NewsService()
        assert service.cache_ttl == 3600

    def test_cache_set_and_get(self):
        """Cache set and get should work."""
        service = NewsService()
        key = "test_key"
        data = (['article1'], {'score': 5})
        service._set_cache(key, data)
        cached = service._get_cache(key)
        assert cached == data

    def test_cache_get_missing_key(self):
        """Cache get for missing key should return None."""
        service = NewsService()
        result = service._get_cache("nonexistent_key")
        assert result is None

    def test_cache_expiry(self):
        """Cache should expire after TTL."""
        service = NewsService()
        service.cache_ttl = 1  # 1 second TTL
        key = "expiry_test"
        data = (['article1'], {'score': 5})
        service._set_cache(key, data)
        # Should be available immediately
        assert service._get_cache(key) is not None
        # Wait for expiry
        time.sleep(1.1)
        assert service._get_cache(key) is None


# === Keywords Tests ===

class TestKeywords:
    """Tests for sentiment keywords."""

    def test_positive_keywords_not_empty(self):
        """POSITIVE_KEYWORDS should not be empty."""
        assert len(POSITIVE_KEYWORDS) > 0

    def test_negative_keywords_not_empty(self):
        """NEGATIVE_KEYWORDS should not be empty."""
        assert len(NEGATIVE_KEYWORDS) > 0

    def test_no_duplicate_keywords(self):
        """Keywords should not have duplicates."""
        assert len(POSITIVE_KEYWORDS) == len(set(POSITIVE_KEYWORDS))
        assert len(NEGATIVE_KEYWORDS) == len(set(NEGATIVE_KEYWORDS))

    def test_keywords_are_lowercase(self):
        """All keywords should be lowercase (for case-insensitive matching)."""
        for kw in POSITIVE_KEYWORDS + NEGATIVE_KEYWORDS:
            assert kw == kw.lower(), f"Keyword not lowercase: {kw}"


# === HTML Cleaning Tests ===

class TestHTMLCleaning:
    """Tests for HTML cleaning utility."""

    def test_clean_html_empty(self):
        """Empty string should return empty."""
        service = NewsService()
        assert service._clean_html('') == ''
        assert service._clean_html(None) == ''

    def test_clean_html_no_tags(self):
        """Plain text should be returned as-is."""
        service = NewsService()
        text = "Just plain text"
        assert service._clean_html(text) == text

    def test_clean_html_with_tags(self):
        """HTML tags should be removed."""
        service = NewsService()
        result = service._clean_html('<p>Hello <b>world</b></p>')
        assert '<p>' not in result
        assert '<b>' not in result
        assert 'Hello' in result
        assert 'world' in result

    def test_clean_html_entities(self):
        """HTML entities should be decoded."""
        service = NewsService()
        result = service._clean_html('Hello &amp; world &lt;test&gt;')
        assert '&amp;' not in result
        assert '&lt;' not in result
        assert '&' in result
        assert '<' in result


# === Singleton Test ===

class TestSingleton:
    """Tests for the news_service singleton."""

    def test_singleton_exists(self):
        """Singleton instance should exist."""
        assert news_service is not None

    def test_singleton_has_methods(self):
        """Singleton should have all required methods."""
        assert hasattr(news_service, 'get_stock_news')
        assert hasattr(news_service, 'get_crypto_news')
        assert hasattr(news_service, 'analyze_sentiment')
