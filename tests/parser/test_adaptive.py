import asyncio

import pytest

from scrapling import Selector
from scrapling.core.storage import StorageSystemMixin
from scrapling.engines.toolbelt.custom import Response


SITE_A_HTML = """
<html><body>
    <a href="/products/1"><p id="fingerprint-a">Site A item</p></a>
</body></html>
"""

SITE_B_HTML = """
<html><body>
    <a href="/items/9"><p id="fingerprint-b">Site B item</p></a>
</body></html>
"""


def _response(url, html, *, storage_file, adaptive_domain=""):
    return Response(
        url=url,
        content=html,
        status=200,
        reason="OK",
        cookies={},
        headers={},
        request_headers={},
        adaptive=True,
        adaptive_domain=adaptive_domain,
        storage_args={"storage_file": storage_file},
    )


class TestAdaptiveDomainPageBase:
    """`adaptive_domain` must namespace fingerprints without replacing the page base URL."""

    def test_response_url_keeps_full_real_url(self, tmp_path):
        db_path = str(tmp_path / "storage.db")
        real_url = "https://shop.example.com/category/electronics?page=2"
        response = _response(real_url, SITE_A_HTML, storage_file=db_path, adaptive_domain="shared.test")

        assert response.url == real_url

    def test_urljoin_uses_real_url_not_adaptive_domain(self, tmp_path):
        db_path = str(tmp_path / "storage.db")
        response = _response(
            "https://shop.example.com/category/electronics",
            SITE_A_HTML,
            storage_file=db_path,
            adaptive_domain="shared.test",
        )

        assert response.urljoin("/products/1") == "https://shop.example.com/products/1"
        assert response.urljoin("relative/page") == "https://shop.example.com/category/relative/page"

    def test_sub_selectors_inherit_real_url_and_storage(self, tmp_path):
        db_path = str(tmp_path / "storage.db")
        response = _response(
            "https://shop.example.com/category/electronics",
            SITE_A_HTML,
            storage_file=db_path,
            adaptive_domain="shared.test",
        )

        link = response.css("a").first
        assert link.urljoin("/products/1") == "https://shop.example.com/products/1"
        assert link._storage is response._storage
        assert link._Selector__adaptive_domain == "shared.test"

    def test_adaptive_domain_becomes_storage_namespace(self, tmp_path):
        db_path = str(tmp_path / "storage.db")
        response = _response(
            "https://shop.example.com/category/electronics",
            SITE_A_HTML,
            storage_file=db_path,
            adaptive_domain="shared.test",
        )

        assert response._storage.namespace == "shared.test"
        StorageSystemMixin._get_base_url.cache_clear()
        assert response._storage._get_base_url() == "shared.test"


class TestAdaptiveDomainFingerprintSharing:
    """Pages with different real URLs but the same `adaptive_domain` share fingerprints."""

    def test_fingerprint_shared_across_real_urls(self, tmp_path):
        db_path = str(tmp_path / "shared.db")
        first = _response(
            "https://shop.example.com/category/electronics",
            SITE_A_HTML,
            storage_file=db_path,
            adaptive_domain="same-site.test",
        )
        # The second page no longer contains the element; only adaptive relocation can find it.
        changed_html = SITE_A_HTML.replace('id="fingerprint-a"', 'id="renamed-a"')
        second = _response(
            "https://new-shop.example.org/deep/path?ref=home",
            changed_html,
            storage_file=db_path,
            adaptive_domain="same-site.test",
        )

        first.css("#fingerprint-a", auto_save=True)
        assert second.retrieve("#fingerprint-a") is not None

    def test_different_adaptive_domains_do_not_pollute_each_other(self, tmp_path):
        db_path = str(tmp_path / "shared.db")
        site_a = _response(
            "https://a.example.com/page",
            SITE_A_HTML,
            storage_file=db_path,
            adaptive_domain="domain-a.test",
        )
        site_b = _response(
            "https://b.example.com/page",
            SITE_B_HTML,
            storage_file=db_path,
            adaptive_domain="domain-b.test",
        )

        site_a.css("#fingerprint-a", auto_save=True)
        site_b.css("#fingerprint-b", auto_save=True)

        # Neither site can read the other one's fingerprints
        assert site_b.retrieve("#fingerprint-a") is None
        assert site_a.retrieve("#fingerprint-b") is None

    def test_storage_file_only_args_no_longer_collapse_into_default(self, tmp_path):
        """Passing only `storage_file` used to drop every site into the 'default' namespace."""
        db_path = str(tmp_path / "storage.db")
        site_a = Selector(
            SITE_A_HTML,
            url="https://a.example.com/path",
            adaptive=True,
            adaptive_domain="dom-a.test",
            storage_args={"storage_file": db_path},
        )
        site_b = Selector(
            SITE_B_HTML,
            url="https://b.example.com/path",
            adaptive=True,
            adaptive_domain="dom-b.test",
            storage_args={"storage_file": db_path},
        )

        site_a.css("#fingerprint-a", auto_save=True)
        assert site_b.retrieve("#fingerprint-a") is None


class TestStorageArgsPrecedence:
    """An explicit `url` inside `storage_args` keeps priority over `adaptive_domain`."""

    def test_explicit_storage_url_wins(self, tmp_path):
        db_path = str(tmp_path / "explicit.db")
        StorageSystemMixin._get_base_url.cache_clear()
        page = Selector(
            SITE_A_HTML,
            url="https://real-page.example.com/path",
            adaptive=True,
            adaptive_domain="ignored-domain.test",
            storage_args={"storage_file": db_path, "url": "https://explicit.example.com/y"},
        )

        # Storage follows the explicit URL's FLD, not the adaptive domain
        assert page._storage.url == "https://explicit.example.com/y"
        assert page._storage.namespace is None
        assert page._storage._get_base_url() == "example.com"
        # ...while the page base URL for link resolution is still untouched
        assert page.url == "https://real-page.example.com/path"

    def test_explicit_namespace_in_storage_args_wins(self, tmp_path):
        db_path = str(tmp_path / "ns.db")
        page = Selector(
            SITE_A_HTML,
            url="https://real-page.example.com/path",
            adaptive=True,
            adaptive_domain="ignored-domain.test",
            storage_args={"storage_file": db_path, "namespace": "explicit-ns.test"},
        )

        assert page._storage.namespace == "explicit-ns.test"
        assert page._storage._get_base_url() == "explicit-ns.test"


class TestLegacyNamespaceBehavior:
    """Without `adaptive_domain`, namespaces keep being derived from the page URL's FLD."""

    def test_fld_derivation_unchanged(self):
        StorageSystemMixin._get_base_url.cache_clear()
        page = Selector(
            SITE_A_HTML,
            url="https://www.example.com/p1",
            adaptive=True,
            storage_args={"storage_file": ":memory:"},
        )
        assert page._storage.namespace is None
        assert page._storage._get_base_url() == "example.com"

    def test_no_url_no_namespace_uses_default(self):
        StorageSystemMixin._get_base_url.cache_clear()
        page = Selector(
            SITE_A_HTML,
            adaptive=True,
            storage_args={"storage_file": ":memory:"},
        )
        assert page._storage._get_base_url() == "default"


class TestParserAdaptive:
    def test_element_relocation(self):
        """Test relocating element after structure change"""
        original_html = """
                <div class="container">
                    <section class="products">
                        <article class="product" id="p1">
                            <h3>Product 1</h3>
                            <p class="description">Description 1</p>
                        </article>
                        <article class="product" id="p2">
                            <h3>Product 2</h3>
                            <p class="description">Description 2</p>
                        </article>
                    </section>
                </div>
                """
        changed_html = """
                <div class="new-container">
                    <div class="product-wrapper">
                        <section class="products">
                            <article class="product new-class" data-id="p1">
                                <div class="product-info">
                                    <h3>Product 1</h3>
                                    <p class="new-description">Description 1</p>
                                </div>
                            </article>
                            <article class="product new-class" data-id="p2">
                                <div class="product-info">
                                    <h3>Product 2</h3>
                                    <p class="new-description">Description 2</p>
                                </div>
                            </article>
                        </section>
                    </div>
                </div>
                """

        old_page = Selector(original_html, url="example.com", adaptive=True)
        new_page = Selector(changed_html, url="example.com", adaptive=True)

        # 'p1' was used as ID and now it's not and all the path elements have changes
        # Also at the same time testing `adaptive` vs combined selectors
        _ = old_page.css("#p1, #p2", auto_save=True)[0]
        relocated = new_page.css("#p1", adaptive=True)

        assert relocated is not None
        assert relocated[0].attrib["data-id"] == "p1"
        assert relocated[0].has_class("new-class")
        assert relocated[0].css(".new-description")[0].text == "Description 1"

    def test_relocation_auto_save_no_match_above_threshold(self):
        """Adaptive relocation with `auto_save=True` must not crash when no element
        clears the `percentage` threshold (relocate() returns an empty list)."""
        original_html = """
                <div class="container">
                    <article class="product" id="target">
                        <h3>Widget</h3>
                        <p class="desc">A widget</p>
                    </article>
                </div>
                """
        # Unrelated structure so nothing can match a high threshold
        changed_html = "<html><body><span>totally unrelated content</span></body></html>"

        old_page = Selector(original_html, url="example.com", adaptive=True)
        new_page = Selector(changed_html, url="example.com", adaptive=True)

        old_page.css("#target", identifier="target", auto_save=True)

        # Before the fix this raised `IndexError: list index out of range` because the
        # guard checked `elements is not None` but relocate() returns [] (never None).
        result = new_page.css(
            "#target", identifier="target", adaptive=True, auto_save=True, percentage=95
        )
        assert list(result) == []

    @pytest.mark.asyncio
    async def test_element_relocation_async(self):
        """Test relocating element after structure change in async mode"""
        original_html = """
                <div class="container">
                    <section class="products">
                        <article class="product" id="p1">
                            <h3>Product 1</h3>
                            <p class="description">Description 1</p>
                        </article>
                        <article class="product" id="p2">
                            <h3>Product 2</h3>
                            <p class="description">Description 2</p>
                        </article>
                    </section>
                </div>
                """
        changed_html = """
                <div class="new-container">
                    <div class="product-wrapper">
                        <section class="products">
                            <article class="product new-class" data-id="p1">
                                <div class="product-info">
                                    <h3>Product 1</h3>
                                    <p class="new-description">Description 1</p>
                                </div>
                            </article>
                            <article class="product new-class" data-id="p2">
                                <div class="product-info">
                                    <h3>Product 2</h3>
                                    <p class="new-description">Description 2</p>
                                </div>
                            </article>
                        </section>
                    </div>
                </div>
                """

        # Simulate async operation
        await asyncio.sleep(0.1)  # Minimal async operation

        old_page = Selector(original_html, url="example.com", adaptive=True)
        new_page = Selector(changed_html, url="example.com", adaptive=True)

        # 'p1' was used as ID and now it's not and all the path elements have changes
        # Also at the same time testing `adaptive` vs combined selectors
        _ = old_page.css("#p1, #p2", auto_save=True)[0]
        relocated = new_page.css("#p1", adaptive=True)

        assert relocated is not None
        assert relocated[0].attrib["data-id"] == "p1"
        assert relocated[0].has_class("new-class")
        assert relocated[0].css(".new-description")[0].text == "Description 1"
