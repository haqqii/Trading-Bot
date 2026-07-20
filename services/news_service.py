"""
News and Sentiment Analysis Service
Fetches news from Google News RSS, Finnhub + NewsAPI and performs sentiment analysis.
"""
import os
import time
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger(__name__)

# API Keys
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY', '')
NEWS_API_KEY = os.getenv('NEWS_API_KEY', '')

# Sentiment Keywords
POSITIVE_KEYWORDS = [
    'bullish', 'surge', 'gain', 'profit', 'rise', 'up', 'growth',
    'positive', 'upgrade', 'buy', 'rally', 'soar', 'jump', 'climb',
    'optimistic', 'recovery', 'rebound', 'breakout', ' outperform',
    'beat', 'exceed', 'growth', 'expansion', 'strong', 'high'
]

NEGATIVE_KEYWORDS = [
    'bearish', 'fall', 'loss', 'drop', 'down', 'decline', 'negative',
    'downgrade', 'sell', 'risk', 'crash', 'plunge', 'sink', 'tumble',
    'pessimistic', 'concern', 'warning', 'cut', 'weak', 'low',
    'underperform', 'miss', 'below', 'fear', 'uncertainty'
]

# HTTP Session with connection pooling
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=10,
    pool_maxsize=10,
    max_retries=2,
    pool_block=False
)
_session.mount('http://', _adapter)
_session.mount('https://', _adapter)


class NewsService:
    """Service for fetching news and analyzing sentiment"""

    def __init__(self):
        self.cache = {}  # Simple in-memory cache
        self.cache_ttl = 3600  # 1 hour for news (news doesn't change frequently)

    def _get_cache(self, key: str) -> Optional[List]:
        """Get from cache if not expired"""
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.cache_ttl:
                return data
        return None

    def _set_cache(self, key: str, data: List):
        """Set cache with timestamp"""
        self.cache[key] = (data, time.time())

    def fetch_finnhub_news(self, ticker: str = None, category: str = 'general') -> List[Dict]:
        """Fetch news from Finnhub API"""
        if not FINNHUB_API_KEY:
            logger.debug("Finnhub API key not set")
            return []

        try:
            # Get recent market news
            url = f"https://finnhub.io/api/v1/news"
            params = {
                'category': category,
                'token': FINNHUB_API_KEY
            }

            resp = _session.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Finnhub news failed: {resp.status_code}")
                return []

            news = resp.json()
            if not news:
                return []

            # Filter for relevant news if ticker provided
            if ticker:
                ticker_upper = ticker.upper().replace('.JK', '')
                relevant = []
                for item in news[:50]:  # Check last 50 news
                    headline = item.get('headline', '').lower()
                    summary = item.get('summary', '').lower()
                    if ticker_upper in headline or ticker_upper in summary:
                        relevant.append(item)
                    # Also check common variations
                    elif any(v in headline or v in summary for v in [ticker_upper.lower()]):
                        relevant.append(item)
                return relevant[:10]  # Max 10 articles

            return news[:20]  # Return last 20 general news

        except Exception as e:
            logger.warning(f"Finnhub news error: {e}")
            return []

    def fetch_newsapi_news(self, query: str) -> List[Dict]:
        """Fetch news from NewsAPI.org"""
        if not NEWS_API_KEY:
            logger.debug("NewsAPI key not set")
            return []

        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': query,
                'apiKey': NEWS_API_KEY,
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': 20
            }

            resp = _session.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"NewsAPI failed: {resp.status_code}")
                return []

            data = resp.json()
            articles = data.get('articles', [])

            # Convert to same format as Finnhub
            result = []
            for article in articles:
                result.append({
                    'headline': article.get('title', ''),
                    'summary': article.get('description', ''),
                    'source': article.get('source', {}).get('name', ''),
                    'datetime': article.get('publishedAt', ''),
                    'url': article.get('url', '')
                })

            return result

        except Exception as e:
            logger.warning(f"NewsAPI error: {e}")
            return []

    def fetch_google_news(self, ticker: str, stock_name: str = None) -> List[Dict]:
        """Fetch news from Google News RSS for Indonesian stock market"""
        articles = []
        ticker_upper = ticker.upper().replace('.JK', '')

        # Search queries to try (Indonesian stock specific)
        queries = [
            f"{ticker_upper} saham",
            f"{ticker_upper} idx",
        ]
        # Add stock name if available
        if stock_name:
            # Extract short name (e.g., "PT Astrindo" from full name)
            short_name = stock_name.split()[0] if stock_name else None
            if short_name and len(short_name) > 2:
                queries.append(f"{short_name} saham Indonesia")

        seen_titles = set()

        for query in queries:
            try:
                encoded_query = quote(query)
                url = (
                    f"https://news.google.com/rss/search"
                    f"?q={encoded_query}"
                    f"&hl=id-ID&gl=ID&ceid=ID:id"
                )

                resp = _session.get(url, timeout=8)  # Reduced from 15s for faster response
                if resp.status_code != 200:
                    continue

                # Parse RSS XML
                root = ET.fromstring(resp.content)

                # Items are inside <channel> element
                channel = root.find('channel')
                if channel is None:
                    continue

                for item in channel.findall('item'):
                    title = item.find('title')
                    link = item.find('link')
                    description = item.find('description')
                    pub_date = item.find('pubDate')
                    source = item.find('source')

                    title_text = title.text if title is not None else ''
                    if not title_text or title_text in seen_titles:
                        continue

                    # Skip if too old (more than 7 days)
                    if pub_date is not None and pub_date.text:
                        try:
                            from email.utils import parsedate_to_datetime
                            pub_dt = parsedate_to_datetime(pub_date.text)
                            if (datetime.now().astimezone() - pub_dt).days > 7:
                                continue
                        except:
                            pass

                    seen_titles.add(title_text)

                    articles.append({
                        'headline': self._clean_html(title_text),
                        'summary': self._clean_html(description.text if description is not None else ''),
                        'source': source.text if source is not None else 'Google News',
                        'datetime': pub_date.text if pub_date is not None else '',
                        'url': link.text if link is not None else ''
                    })

                    if len(articles) >= 15:  # Max 15 articles
                        break

                if len(articles) >= 15:
                    break

            except Exception as e:
                logger.debug(f"Google News RSS error for query '{query}': {e}")
                continue

        return articles

    def _clean_html(self, text: str) -> str:
        """Remove HTML tags from text"""
        if not text:
            return ''
        import re
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Decode common HTML entities
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
        return text.strip()

    def analyze_sentiment(self, articles: List[Dict]) -> Dict:
        """
        Analyze sentiment from articles.
        Returns dict with:
        - overall: 'positive', 'negative', 'neutral'
        - score: -10 to +10
        - summary: brief description
        - headline_count: number of articles analyzed
        """
        if not articles:
            return {
                'overall': 'neutral',
                'score': 0,
                'summary': 'Tidak ada berita terbaru',
                'headline_count': 0,
                'positive_count': 0,
                'negative_count': 0
            }

        positive_count = 0
        negative_count = 0
        total_score = 0
        headlines_analyzed = []
        all_headlines_list = []

        for article in articles[:10]:  # Analyze up to 10 articles
            headline = article.get('headline', '').lower()
            summary = article.get('summary', '') or article.get('description', '') or ''
            summary = summary.lower()
            text = f"{headline} {summary}"

            article_score = 0

            # Count positive keywords
            for kw in POSITIVE_KEYWORDS:
                if kw in text:
                    article_score += 1

            # Count negative keywords
            for kw in NEGATIVE_KEYWORDS:
                if kw in text:
                    article_score -= 1

            total_score += article_score

            if article_score > 0:
                positive_count += 1
            elif article_score < 0:
                negative_count += 1

            # Keep track of headlines for summary
            if abs(article_score) >= 2:
                headlines_analyzed.append({
                    'headline': article.get('headline', '')[:100],
                    'score': article_score
                })

            # Always keep all headlines (for display)
            all_headlines_list.append({
                'headline': article.get('headline', '')[:100],
                'score': article_score,
                'source': article.get('source', '')
            })

        # Determine overall sentiment
        avg_score = total_score / len(articles)

        if avg_score > 0.5:
            overall = 'positive'
            emoji = '🟢'
        elif avg_score < -0.5:
            overall = 'negative'
            emoji = '🔴'
        else:
            overall = 'neutral'
            emoji = '🟡'

        # Generate summary
        if positive_count > negative_count:
            summary = f"Berita cenderung positif ({positive_count} positif vs {negative_count} negatif)"
        elif negative_count > positive_count:
            summary = f"Berita cenderung negatif ({negative_count} negatif vs {positive_count} positif)"
        else:
            summary = "Berita cenderung netral"

        return {
            'overall': overall,
            'score': round(avg_score, 1),
            'summary': summary,
            'headline_count': len(articles),
            'positive_count': positive_count,
            'negative_count': negative_count,
            'emoji': emoji,
            'top_headlines': headlines_analyzed[:3],
            'all_headlines': all_headlines_list[:5]  # Include all headlines for display
        }

    def get_stock_news(self, ticker: str, stock_name: str = None) -> Tuple[List[Dict], Dict]:
        """
        Get news and sentiment for a stock (Indonesian IDX stocks).
        Returns (articles, sentiment)
        """
        cache_key = f"stock_news_{ticker}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        articles = []
        clean_ticker = ticker.replace('.JK', '').upper()

        # 1. Try Google News RSS first (best for Indonesian stocks)
        google_news = self.fetch_google_news(clean_ticker, stock_name)
        articles.extend(google_news)
        logger.info(f"[NEWS] Google News for {ticker}: {len(google_news)} articles")

        # 2. Fallback to Finnhub (for international coverage)
        if not articles:
            finnhub_news = self.fetch_finnhub_news(clean_ticker, 'general')
            articles.extend(finnhub_news)
            sector_news = self.fetch_finnhub_news(clean_ticker, 'forex')
            articles.extend(sector_news)

        # 3. Fallback to NewsAPI
        if not articles and NEWS_API_KEY:
            newsapi_news = self.fetch_newsapi_news(f"{clean_ticker} stock Indonesia")
            articles.extend(newsapi_news)

        # Remove duplicates based on headline
        seen = set()
        unique_articles = []
        for a in articles:
            h = a.get('headline', '')[:50]
            if h and h not in seen:
                seen.add(h)
                unique_articles.append(a)

        # Analyze sentiment
        sentiment = self.analyze_sentiment(unique_articles)

        # Cache results
        self._set_cache(cache_key, (unique_articles, sentiment))

        return unique_articles, sentiment

    def get_crypto_news(self, ticker: str) -> Tuple[List[Dict], Dict]:
        """Get news and sentiment for a crypto"""
        cache_key = f"crypto_news_{ticker}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        articles = []

        # Clean ticker
        clean_ticker = ticker.replace('-USD', '').replace('-USDT', '').upper()

        # Fetch from Finnhub crypto category
        finnhub_news = self.fetch_finnhub_news(clean_ticker, 'crypto')
        articles.extend(finnhub_news)

        # Fallback to NewsAPI
        if not articles and NEWS_API_KEY:
            newsapi_news = self.fetch_newsapi_news(f"{clean_ticker} cryptocurrency bitcoin")
            articles.extend(newsapi_news)

        # Remove duplicates
        seen = set()
        unique_articles = []
        for a in articles:
            h = a.get('headline', '')[:50]
            if h and h not in seen:
                seen.add(h)
                unique_articles.append(a)

        # Analyze sentiment
        sentiment = self.analyze_sentiment(unique_articles)

        # Cache results
        self._set_cache(cache_key, (unique_articles, sentiment))

        return unique_articles, sentiment


# Singleton instance
news_service = NewsService()
