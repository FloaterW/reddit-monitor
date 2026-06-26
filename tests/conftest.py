"""Shared fixtures for reddit-digest tests.

All HTML is static — no live Reddit requests.
"""

import pytest


@pytest.fixture()
def post_html():
    """Minimal old.reddit listing HTML with two post 'thing' divs."""
    return """
    <html><body>
    <div class="thing" data-type="link" data-fullname="t3_abc123"
         data-score="42" data-comments-count="7" data-author="testuser"
         data-subreddit="churning" data-url="https://example.com"
         data-permalink="/r/churning/comments/abc123/test_post/"
         data-timestamp="1719244800000" data-promoted="false">
      <a class="title may-blank" href="/r/churning/comments/abc123/">First Post Title</a>
    </div>
    <div class="thing" data-type="link" data-fullname="t3_def456"
         data-score="10" data-comments-count="3" data-author="otheruser"
         data-subreddit="churning" data-url="https://example.com/2"
         data-permalink="/r/churning/comments/def456/second_post/"
         data-timestamp="1719158400000" data-promoted="false">
      <a class="title may-blank" href="/r/churning/comments/def456/">Second Post Title</a>
    </div>
    <div class="thing" data-type="link" data-fullname="t3_promo"
         data-score="0" data-comments-count="0" data-author="ad_account"
         data-subreddit="churning" data-url="https://ad.example.com"
         data-permalink="/r/churning/comments/promo/buy_stuff/"
         data-timestamp="1719244800000" data-promoted="true">
      <a class="title may-blank" href="/r/churning/comments/promo/">Promoted Ad</a>
    </div>
    </body></html>
    """


@pytest.fixture()
def comment_html():
    """Minimal old.reddit comment page HTML with two comments (one nested)."""
    return """
    <html><body>
    <div class="commentarea">
      <div class="sitetable nestedlisting" id="siteTable_t3_abc123">
        <div class="thing comment" data-type="comment" data-fullname="t1_aaa111"
             data-author="alice">
          <div class="entry">
            <span class="score likes">15 points</span>
            <time datetime="2025-06-24T12:00:00+00:00">1 hour ago</time>
            <div class="md"><p>Chase Sapphire bonus is 80k right now</p></div>
          </div>
          <div class="child">
            <div class="sitetable" id="siteTable_t1_aaa111">
              <div class="thing comment" data-type="comment" data-fullname="t1_bbb222"
                   data-author="bob">
                <div class="entry">
                  <span class="score likes">5 points</span>
                  <time datetime="2025-06-24T13:00:00+00:00">30 min ago</time>
                  <div class="md"><p>Can confirm, applied yesterday for the Chase ink card</p></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </body></html>
    """


@pytest.fixture()
def search_html():
    """Minimal old.reddit search results page HTML."""
    return """
    <html><body>
    <div class="search-result-listing">
      <div class="search-result search-result-link">
        <a class="search-title may-blank" href="/r/churning/comments/xyz/great_deal/">Great Amex Deal</a>
        <span class="search-score">55</span>
        <a class="search-comments may-blank">12 comments</a>
        <a class="author">dealfinder</a>
        <a class="search-subreddit-link" href="/r/churning">r/churning</a>
        <time datetime="2025-06-20T10:00:00+00:00">4 days ago</time>
        <div class="search-result-body">Amex Gold 90k SUB is back, targeted offer via email.</div>
      </div>
      <div class="search-result search-result-link">
        <a class="search-title may-blank" href="https://old.reddit.com/r/CreditCards/comments/qrs/new_card/">New Card Launch</a>
        <span class="search-score">30</span>
        <a class="search-comments may-blank">5 comments</a>
        <a class="author">cardguy</a>
        <a class="search-subreddit-link" href="/r/CreditCards">r/CreditCards</a>
        <time datetime="2025-06-19T08:00:00+00:00">5 days ago</time>
        <div class="search-result-body">Capital One launched a new premium card.</div>
      </div>
    </div>
    </body></html>
    """
