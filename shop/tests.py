import io
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from .models import (
    AnnouncementMessage,
    Collection,
    CollectionItem,
    Colour,
    CustomDesignRequest,
    Offer,
    Order,
    Product,
    SiteSettings,
    Size,
    Spotlight,
    SpotlightItem,
    TeeStyle,
)

MEDIA = tempfile.mkdtemp()


def png(name="art.png", size=(400, 400)):
    buf = io.BytesIO()
    Image.new("RGBA", size, (200, 30, 30, 255)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA, REST_FRAMEWORK={
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_THROTTLE_RATES": {"anon": "1000/minute", "submissions": "1000/minute"},
    "UNAUTHENTICATED_USER": None,
})
class StoreApiTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        site = SiteSettings.load()
        site.shipping_fee = Decimal("79")
        site.free_shipping_threshold = Decimal("999")
        site.save()
        self.black = Colour.objects.create(name="Black", hex_code="#111111")
        self.white = Colour.objects.create(name="White", hex_code="#ffffff")
        self.m = Size.objects.create(name="M", sort_order=20)
        self.l = Size.objects.create(name="L", sort_order=30)
        self.kerala = Collection.objects.create(name="Kerala", sort_order=20)
        self.cinema = Collection.objects.create(name="Cinema", sort_order=10)
        self.a = Product.objects.create(name="Alpha", price=Decimal("500"), sort_order=10)
        self.b = Product.objects.create(name="Bravo", price=Decimal("700"), sort_order=20)
        for p in (self.a, self.b):
            p.colours.set([self.black, self.white])
            p.sizes.set([self.m, self.l])
        CollectionItem.objects.create(collection=self.kerala, product=self.a, sort_order=20)
        CollectionItem.objects.create(collection=self.kerala, product=self.b, sort_order=10)
        self.style = TeeStyle.objects.create(name="Oversized", base_price=Decimal("449"), front_print_price=Decimal("150"), back_print_price=Decimal("200"))
        self.style.colours.set([self.black])
        self.style.sizes.set([self.m, self.l])

    # -- ordering configured in admin -------------------------------------------
    def test_collections_and_products_follow_sort_order(self):
        data = self.client.get("/api/collections/").json()
        self.assertEqual([c["slug"] for c in data], ["cinema", "kerala"])
        data = self.client.get("/api/collections/kerala/").json()
        self.assertEqual([p["slug"] for p in data["products"]], ["bravo", "alpha"])
        CollectionItem.objects.filter(product=self.a).update(sort_order=1)
        data = self.client.get("/api/collections/kerala/").json()
        self.assertEqual([p["slug"] for p in data["products"]], ["alpha", "bravo"])

    def test_messages_respect_schedule_and_placement(self):
        AnnouncementMessage.objects.create(text="Bar", placement="bar")
        AnnouncementMessage.objects.create(text="Scroll", placement="marquee")
        AnnouncementMessage.objects.create(text="Off", placement="bar", is_active=False)
        data = self.client.get("/api/home/").json()
        self.assertEqual([m["text"] for m in data["announcements"]], ["Bar"])
        self.assertEqual([m["text"] for m in data["marquee"]], ["Scroll"])

    # -- pricing -------------------------------------------------------------------
    def test_quote_uses_server_prices_and_shipping(self):
        items = [{"type": "product", "slug": "alpha", "colour": "Black", "size": "M", "quantity": 1, "price": "1"}]
        q = self.client.post("/api/cart/quote/", {"items": items}, content_type="application/json").json()
        self.assertEqual(q["subtotal"], "500.00")
        self.assertEqual(Decimal(q["shipping"]), Decimal("79"))
        items[0]["quantity"] = 2
        q = self.client.post("/api/cart/quote/", {"items": items}, content_type="application/json").json()
        self.assertEqual(Decimal(q["shipping"]), Decimal("0"))
        self.assertEqual(q["total"], "1000.00")

    def test_invalid_size_rejected(self):
        items = [{"type": "product", "slug": "alpha", "colour": "Black", "size": "XXXL", "quantity": 1}]
        r = self.client.post("/api/cart/quote/", {"items": items}, content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_coupon_percent_with_cap_and_minimum(self):
        Offer.objects.create(title="Ten", coupon_code="avten", discount_value=Decimal("10"), max_discount=Decimal("60"))
        Offer.objects.create(title="Big", coupon_code="BIG", discount_type="flat", discount_value=Decimal("150"), min_order_amount=Decimal("1499"))
        items = [{"type": "product", "slug": "bravo", "colour": "Black", "size": "L", "quantity": 1}]
        q = self.client.post("/api/cart/quote/", {"items": items, "coupon_code": "AVTEN"}, content_type="application/json").json()
        self.assertEqual(Decimal(q["discount"]), Decimal("60"))
        q = self.client.post("/api/cart/quote/", {"items": items, "coupon_code": "big"}, content_type="application/json").json()
        self.assertEqual(Decimal(q["discount"]), Decimal("0"))
        self.assertIn("more", q["coupon_message"])

    # -- custom design approval flow ---------------------------------------------------
    def submit_design(self, **overrides):
        data = {
            "tee_style": self.style.id, "colour": self.black.id, "size": self.m.id, "placement": "both", "quantity": 2,
            "customer_name": "Asha", "customer_email": "asha@example.com", "customer_phone": "+91 98765 43210",
            "front_artwork": png("f.png"), "back_artwork": png("b.png"), "front_mockup": png("fm.png"),
        }
        data.update(overrides)
        return self.client.post("/api/custom-designs/", data)

    def test_design_submission_prices_and_validates(self):
        r = self.submit_design()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["status"], "pending")
        self.assertEqual(Decimal(body["unit_price"]), Decimal("799"))
        self.assertFalse(body["can_order"])
        self.assertEqual(CustomDesignRequest.objects.get().customer_phone, "919876543210")

        r = self.submit_design(colour=self.white.id)
        self.assertEqual(r.status_code, 400)
        r = self.submit_design(placement="back", back_artwork="")
        self.assertEqual(r.status_code, 400)
        r = self.submit_design(front_artwork=SimpleUploadedFile("x.png", b"not an image", content_type="image/png"))
        self.assertEqual(r.status_code, 400)

    def test_design_must_be_approved_before_ordering(self):
        token = self.submit_design().json()["token"]
        customer = {
            "customer_name": "Asha", "customer_email": "asha@example.com", "customer_phone": "9876543210",
            "address_line1": "1 Marine Drive", "city": "Kochi", "state": "Kerala", "pincode": "682001",
        }
        order = {**customer, "items": [{"type": "custom", "token": token}]}
        r = self.client.post("/api/orders/", order, content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not approved", str(r.json()))

        design = CustomDesignRequest.objects.get(token=token)
        design.status = CustomDesignRequest.APPROVED
        design.approved_price = Decimal("750")
        design.save()

        r = self.client.post("/api/orders/", order, content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["total"], "1500.00")
        design.refresh_from_db()
        self.assertEqual(design.status, CustomDesignRequest.ORDERED)

        # The same approved design cannot be ordered twice.
        r = self.client.post("/api/orders/", order, content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.count(), 1)

    def test_status_page_and_admin_pages_load(self):
        token = self.submit_design().json()["token"]
        r = self.client.get(f"/api/custom-designs/{token}/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["front_mockup"].startswith("http"))

        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pass12345")
        self.client.force_login(user)
        for url in [
            "/admin/shop/product/", "/admin/shop/collection/", f"/admin/shop/collection/{self.kerala.pk}/change/",
            "/admin/shop/announcementmessage/", "/admin/shop/offer/", "/admin/shop/customdesignrequest/",
            f"/admin/shop/customdesignrequest/{CustomDesignRequest.objects.get().pk}/change/", "/admin/shop/order/",
            "/admin/shop/teestyle/", f"/admin/shop/product/{self.a.pk}/change/",
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/admin/shop/sitesettings/").status_code, 302)

    def test_admin_approve_action(self):
        self.submit_design()
        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pass12345")
        self.client.force_login(user)
        design = CustomDesignRequest.objects.get()
        r = self.client.post("/admin/shop/customdesignrequest/", {"action": "approve_designs", "_selected_action": [design.pk]})
        self.assertEqual(r.status_code, 302)
        design.refresh_from_db()
        self.assertEqual(design.status, "approved")
        self.assertIsNotNone(design.reviewed_at)

    # -- spotlight carousel ---------------------------------------------------------------
    def test_spotlight_cards_follow_admin_order_and_schedule(self):
        self.assertIsNone(self.client.get("/api/home/").json()["spotlight"])
        spot = Spotlight.objects.create(name="Xmas", title="Christmas tees", hide_main_hero=True)
        SpotlightItem.objects.create(spotlight=spot, product=self.a, sort_order=20)
        SpotlightItem.objects.create(spotlight=spot, product=self.b, sort_order=10)
        data = self.client.get("/api/home/").json()["spotlight"]
        self.assertEqual([c["slug"] for c in data["cards"]], ["bravo", "alpha"])
        self.assertTrue(data["hide_main_hero"])
        # Falls back to the linked collection when no cards are set.
        SpotlightItem.objects.all().delete()
        spot.collection = self.kerala
        spot.save()
        data = self.client.get("/api/home/").json()["spotlight"]
        self.assertEqual([c["slug"] for c in data["cards"]], ["bravo", "alpha"])
        # Ended spotlights disappear.
        from django.utils import timezone
        spot.ends_at = timezone.now() - timezone.timedelta(minutes=1)
        spot.save()
        self.assertIsNone(self.client.get("/api/home/").json()["spotlight"])

    def test_spotlight_admin_pages_load(self):
        spot = Spotlight.objects.create(name="Xmas")
        SpotlightItem.objects.create(spotlight=spot, product=self.a)
        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pass12345")
        self.client.force_login(user)
        for url in ["/admin/shop/spotlight/", f"/admin/shop/spotlight/{spot.pk}/change/", "/admin/shop/spotlight/add/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
