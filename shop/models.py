import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]


def validate_upload_size(file):
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    if file.size > limit:
        raise ValidationError(f"File is larger than {settings.MAX_UPLOAD_MB} MB.")


def _upload_path(folder, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return f"{folder}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{ext}"


# Module level functions so migrations can serialize them.
def upload_hero(_instance, filename):
    return _upload_path("site", filename)


def upload_collection(_instance, filename):
    return _upload_path("collections", filename)


def upload_product(_instance, filename):
    return _upload_path("products", filename)


def upload_offer(_instance, filename):
    return _upload_path("offers", filename)


def upload_design(_instance, filename):
    return _upload_path("custom-designs", filename)


class Orderable(models.Model):
    """Lower numbers show first. Edit the number in the admin list to reorder."""

    sort_order = models.PositiveIntegerField(default=100, db_index=True, help_text="Lower shows first.")

    class Meta:
        abstract = True


class LiveWindow(models.Model):
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Optional. Leave empty to start now.")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Optional. Leave empty to run until switched off.")

    class Meta:
        abstract = True

    def is_live(self, now=None):
        now = now or timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at < now:
            return False
        return True

    @classmethod
    def live_q(cls, now=None):
        now = now or timezone.now()
        return (
            models.Q(is_active=True)
            & (models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now))
            & (models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now))
        )


# ---------------------------------------------------------------------------
# Site content
# ---------------------------------------------------------------------------
class SiteSettings(models.Model):
    store_name = models.CharField(max_length=80, default="AV Apparels")
    tagline = models.CharField(max_length=160, default="Heavyweight tees, made to be noticed.")

    hero_eyebrow = models.CharField(max_length=80, blank=True, default="New season drop")
    hero_title = models.CharField(max_length=120, default="Wear your")
    hero_highlight = models.CharField(max_length=60, blank=True, default="story", help_text="Shown in italics after the title.")
    hero_subtitle = models.TextField(blank=True, default="240 GSM oversized tees, printed in Kerala and shipped across India.")
    hero_image = models.ImageField(upload_to=upload_hero, blank=True)
    hero_cta_text = models.CharField(max_length=40, default="Shop the drop")
    hero_cta_link = models.CharField(max_length=200, default="collection.html")

    custom_section_title = models.CharField(max_length=120, default="Design your own tee")
    custom_section_text = models.TextField(
        blank=True,
        default="Pick a fit, colour and size, upload your artwork and our team approves the print before you pay.",
    )

    whatsapp_number = models.CharField(
        max_length=15,
        blank=True,
        validators=[RegexValidator(r"^\d{10,15}$", "Digits only, with country code, e.g. 919876543210.")],
        help_text="Digits with country code, e.g. 919876543210.",
    )
    instagram_url = models.URLField(blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)

    shipping_fee = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("79"))
    free_shipping_threshold = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("999"), help_text="Orders at or above this ship free. Use 0 for always free."
    )
    footer_about = models.TextField(blank=True, default="Independent streetwear label from Kerala.")

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return "Site settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def shipping_for(self, amount: Decimal) -> Decimal:
        free = Decimal("0.00")
        if amount <= 0 or self.free_shipping_threshold == 0:
            return free
        if amount >= self.free_shipping_threshold:
            return free
        return Decimal(self.shipping_fee).quantize(Decimal("0.01"))


class AnnouncementMessage(Orderable, LiveWindow):
    BAR = "bar"
    MARQUEE = "marquee"
    PLACEMENTS = [(BAR, "Top announcement bar (rotates)"), (MARQUEE, "Scrolling marquee strip")]

    text = models.CharField(max_length=160)
    link = models.CharField(max_length=200, blank=True, help_text="Optional page or URL to open on click.")
    placement = models.CharField(max_length=10, choices=PLACEMENTS, default=BAR)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Running message"
        verbose_name_plural = "Running messages"

    def __str__(self):
        return self.text


class Offer(Orderable, LiveWindow):
    PERCENT = "percent"
    FLAT = "flat"
    DISCOUNT_TYPES = [(PERCENT, "Percent off"), (FLAT, "Flat amount off")]

    title = models.CharField(max_length=100)
    subtitle = models.CharField(max_length=200, blank=True)
    badge_text = models.CharField(max_length=30, blank=True, help_text="Short label, e.g. 'Flat 20% off'.")
    image = models.ImageField(upload_to=upload_offer, blank=True)
    link = models.CharField(max_length=200, blank=True)
    show_on_home = models.BooleanField(default=True)

    coupon_code = models.CharField(
        max_length=30, blank=True, null=True, unique=True, help_text="Optional. Customers type this at checkout."
    )
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPES, default=PERCENT)
    discount_value = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"), validators=[MinValueValidator(0)])
    min_order_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    max_discount = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, help_text="Optional cap for percent discounts."
    )

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.title

    def clean(self):
        if self.coupon_code:
            self.coupon_code = self.coupon_code.strip().upper()
        else:
            self.coupon_code = None
        if self.discount_type == self.PERCENT and self.discount_value > 100:
            raise ValidationError({"discount_value": "Percent discount cannot exceed 100."})

    def save(self, *args, **kwargs):
        self.coupon_code = self.coupon_code.strip().upper() if self.coupon_code else None
        super().save(*args, **kwargs)

    def discount_for(self, subtotal: Decimal) -> Decimal:
        if subtotal < self.min_order_amount:
            return Decimal("0")
        if self.discount_type == self.PERCENT:
            amount = (subtotal * self.discount_value / Decimal("100")).quantize(Decimal("0.01"))
            if self.max_discount:
                amount = min(amount, self.max_discount)
        else:
            amount = self.discount_value
        return min(amount, subtotal)


class FeatureHighlight(Orderable):
    icon = models.CharField(max_length=8, default="★", help_text="An emoji or short symbol.")
    title = models.CharField(max_length=60)
    text = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Why-us highlight"

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
class Colour(Orderable):
    name = models.CharField(max_length=40, unique=True)
    hex_code = models.CharField(max_length=7, default="#000000", validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use #RRGGBB.")])

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Size(Orderable):
    name = models.CharField(max_length=10, unique=True)
    chest_inches = models.CharField(max_length=20, blank=True, help_text="Optional, for the size chart.")

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class Collection(Orderable):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=90, unique=True, blank=True)
    eyebrow = models.CharField(max_length=60, blank=True, help_text="Small label above the title on the home page.")
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to=upload_collection, blank=True)
    is_active = models.BooleanField(default=True)
    show_on_home = models.BooleanField(default=True, help_text="Show as a product row on the home page.")
    show_in_nav = models.BooleanField(default=True, help_text="Show in the header menu.")
    show_as_tile = models.BooleanField(default=True, help_text="Show in the 'Shop by theme' tiles.")
    home_product_limit = models.PositiveSmallIntegerField(default=8)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Collection, self.name, self.pk)
        super().save(*args, **kwargs)


class Product(Orderable):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    short_description = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(0)])
    compare_at_price = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, help_text="Original price, shown struck through."
    )
    fit = models.CharField(max_length=40, blank=True, default="Oversized")
    fabric = models.CharField(max_length=80, blank=True, default="240 GSM, 100% cotton")
    badge_text = models.CharField(max_length=24, blank=True, help_text="e.g. 'New', 'Limited'.")
    colours = models.ManyToManyField(Colour, blank=True, related_name="products")
    sizes = models.ManyToManyField(Size, blank=True, related_name="products")
    collections = models.ManyToManyField(Collection, through="CollectionItem", related_name="products", blank=True)
    is_active = models.BooleanField(default=True)
    is_new_arrival = models.BooleanField(default=False)
    is_bestseller = models.BooleanField(default=False)
    in_stock = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Product, self.name, self.pk)
        super().save(*args, **kwargs)

    @property
    def savings(self):
        if self.compare_at_price and self.compare_at_price > self.price:
            return self.compare_at_price - self.price
        return Decimal("0")


class CollectionItem(Orderable):
    """A product's place inside one collection. Reorder from the collection page."""

    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="collection_items")

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = [("collection", "product")]
        verbose_name = "Product in collection"
        verbose_name_plural = "Products in this collection (reorder with the Sort order number)"

    def __str__(self):
        return f"{self.product} in {self.collection}"


class ProductImage(Orderable):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to=upload_product, validators=[FileExtensionValidator(IMAGE_EXTENSIONS), validate_upload_size])
    alt_text = models.CharField(max_length=140, blank=True)
    colour = models.ForeignKey(Colour, on_delete=models.SET_NULL, null=True, blank=True, help_text="Optional: show when this colour is picked.")

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"Image for {self.product}"


# ---------------------------------------------------------------------------
# Custom tee designer
# ---------------------------------------------------------------------------
class TeeStyle(Orderable):
    name = models.CharField(max_length=60, help_text="e.g. Oversized, Regular fit, Acid wash.")
    description = models.CharField(max_length=200, blank=True)
    base_price = models.DecimalField(max_digits=8, decimal_places=2, help_text="Price of the blank tee.")
    front_print_price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("150"))
    back_print_price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("200"))
    colours = models.ManyToManyField(Colour, related_name="tee_styles")
    sizes = models.ManyToManyField(Size, related_name="tee_styles")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Custom tee style"

    def __str__(self):
        return self.name

    def price_for(self, placement: str) -> Decimal:
        price = self.base_price
        if placement in (CustomDesignRequest.FRONT, CustomDesignRequest.BOTH):
            price += self.front_print_price
        if placement in (CustomDesignRequest.BACK, CustomDesignRequest.BOTH):
            price += self.back_print_price
        return price


class CustomDesignRequest(models.Model):
    FRONT, BACK, BOTH = "front", "back", "both"
    PLACEMENTS = [(FRONT, "Front"), (BACK, "Back"), (BOTH, "Front and back")]

    PENDING = "pending"
    APPROVED = "approved"
    CHANGES = "changes_requested"
    REJECTED = "rejected"
    ORDERED = "ordered"
    STATUSES = [
        (PENDING, "Pending review"),
        (APPROVED, "Approved"),
        (CHANGES, "Changes requested"),
        (REJECTED, "Rejected"),
        (ORDERED, "Ordered"),
    ]

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tee_style = models.ForeignKey(TeeStyle, on_delete=models.PROTECT)
    colour = models.ForeignKey(Colour, on_delete=models.PROTECT)
    size = models.ForeignKey(Size, on_delete=models.PROTECT)
    placement = models.CharField(max_length=10, choices=PLACEMENTS, default=FRONT)
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])

    image_validators = [FileExtensionValidator(IMAGE_EXTENSIONS), validate_upload_size]
    front_artwork = models.ImageField(upload_to=upload_design, blank=True, validators=image_validators)
    back_artwork = models.ImageField(upload_to=upload_design, blank=True, validators=image_validators)
    front_mockup = models.ImageField(upload_to=upload_design, blank=True, help_text="Preview rendered in the browser.")
    back_mockup = models.ImageField(upload_to=upload_design, blank=True)
    notes = models.TextField(blank=True)

    customer_name = models.CharField(max_length=100)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=15)

    status = models.CharField(max_length=20, choices=STATUSES, default=PENDING, db_index=True)
    estimated_price = models.DecimalField(max_digits=8, decimal_places=2, help_text="Per tee, calculated when submitted.")
    approved_price = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, help_text="Per tee. Leave empty to use the estimated price."
    )
    admin_note = models.TextField(blank=True, help_text="Shown to the customer on the status page.")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Custom design request"

    def __str__(self):
        return f"{self.reference} ({self.get_status_display()})"

    @property
    def reference(self):
        return f"CD-{str(self.token)[:8].upper()}"

    @property
    def unit_price(self) -> Decimal:
        return self.approved_price if self.approved_price is not None else self.estimated_price

    def clean(self):
        if self.placement in (self.FRONT, self.BOTH) and not self.front_artwork:
            raise ValidationError({"front_artwork": "Front artwork is required for this placement."})
        if self.placement in (self.BACK, self.BOTH) and not self.back_artwork:
            raise ValidationError({"back_artwork": "Back artwork is required for this placement."})


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class Order(models.Model):
    PLACED, CONFIRMED, SHIPPED, DELIVERED, CANCELLED = "placed", "confirmed", "shipped", "delivered", "cancelled"
    STATUSES = [
        (PLACED, "Placed"),
        (CONFIRMED, "Confirmed"),
        (SHIPPED, "Shipped"),
        (DELIVERED, "Delivered"),
        (CANCELLED, "Cancelled"),
    ]
    PAYMENT_METHODS = [("cod", "Cash on delivery"), ("upi_on_call", "UPI link on confirmation")]
    PAYMENT_STATUSES = [("pending", "Pending"), ("paid", "Paid"), ("refunded", "Refunded")]

    number = models.CharField(max_length=24, unique=True, editable=False)
    customer_name = models.CharField(max_length=100)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=15)
    address_line1 = models.CharField(max_length=200)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80)
    pincode = models.CharField(max_length=6, validators=[RegexValidator(r"^\d{6}$", "Enter a 6 digit PIN code.")])
    notes = models.TextField(blank=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=10, decimal_places=2)
    coupon_code = models.CharField(max_length=30, blank=True)

    status = models.CharField(max_length=12, choices=STATUSES, default=PLACED, db_index=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="cod")
    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATUSES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = f"AV{timezone.now():%y%m%d}-{uuid.uuid4().hex[:5].upper()}"
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    custom_design = models.ForeignKey(CustomDesignRequest, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=160)
    colour = models.CharField(max_length=40, blank=True)
    size = models.CharField(max_length=10, blank=True)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.name}"


def _unique_slug(model, value, pk=None):
    base = slugify(value)[:80] or uuid.uuid4().hex[:8]
    slug, n = base, 2
    while model.objects.filter(slug=slug).exclude(pk=pk).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


# ---------------------------------------------------------------------------
# Spotlight carousel (seasonal or featured drop at the top of the home page)
# ---------------------------------------------------------------------------
def upload_spotlight(_instance, filename):
    return _upload_path("spotlight", filename)


class Spotlight(Orderable, LiveWindow):
    """A swipeable 3D card carousel. The first live spotlight shows on the home page."""

    name = models.CharField(max_length=80, help_text="Internal name, e.g. 'Christmas 2026'.")
    eyebrow = models.CharField(max_length=60, blank=True, default="New · Training drop", help_text="Text in the pill above the title.")
    title = models.CharField(max_length=80, default="Festive tees")
    highlight = models.CharField(max_length=60, blank=True, default="are here.", help_text="Second line, shown in the accent colour.")
    description = models.TextField(blank=True)
    ring_text = models.CharField(
        max_length=80, blank=True, default="FESTIVE ✦ HEAVYWEIGHT ✦ AV APPARELS ✦",
        help_text="Words that circle around the cards. Keep it under about 40 characters.",
    )
    accent_colour = models.CharField(max_length=7, default="#B8E84F", validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use #RRGGBB.")])
    pill_colour = models.CharField(max_length=7, default="#E0662A", validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use #RRGGBB.")])
    cta_text = models.CharField(max_length=40, blank=True, default="Shop the collection")
    collection = models.ForeignKey(
        Collection, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Optional. 'Shop' button opens this collection. If no cards are added below, its products are used.",
    )
    autoplay_seconds = models.PositiveSmallIntegerField(default=4, help_text="0 turns auto-rotate off.")
    show_stars = models.BooleanField(default=True, help_text="Twinkling stars in the background.")
    hide_main_hero = models.BooleanField(default=False, help_text="Show only this spotlight at the top, without the regular hero.")

    class Meta:
        ordering = ["sort_order", "-id"]

    def __str__(self):
        return self.name


class SpotlightItem(Orderable):
    spotlight = models.ForeignKey(Spotlight, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    image = models.ImageField(
        upload_to=upload_spotlight, blank=True,
        validators=[FileExtensionValidator(IMAGE_EXTENSIONS), validate_upload_size],
        help_text="Optional lifestyle photo for this card. Portrait 4:5 looks best. Uses the product photo if empty.",
    )

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Card"
        verbose_name_plural = "Cards (reorder with the Sort order number)"

    def __str__(self):
        return f"{self.product} in {self.spotlight}"
