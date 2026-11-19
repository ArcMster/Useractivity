from django.conf import settings
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from .models import (
    AnnouncementMessage,
    Collection,
    Colour,
    CustomDesignRequest,
    FeatureHighlight,
    Offer,
    Order,
    Product,
    SiteSettings,
    Size,
    TeeStyle,
)


def media_url(request, field):
    if not field:
        return None
    try:
        url = field.url
    except ValueError:
        return None
    return request.build_absolute_uri(url) if request else url


class MediaUrlMixin:
    def media(self, field):
        return media_url(self.context.get("request"), field)


class ColourSerializer(serializers.ModelSerializer):
    class Meta:
        model = Colour
        fields = ["id", "name", "hex_code"]


class SizeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Size
        fields = ["id", "name", "chest_inches"]


class SiteSettingsSerializer(MediaUrlMixin, serializers.ModelSerializer):
    hero_image = serializers.SerializerMethodField()

    class Meta:
        model = SiteSettings
        exclude = ["id"]

    def get_hero_image(self, obj):
        return self.media(obj.hero_image)


class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnnouncementMessage
        fields = ["id", "text", "link", "placement"]


class OfferSerializer(MediaUrlMixin, serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = Offer
        fields = ["id", "title", "subtitle", "badge_text", "image", "link", "coupon_code", "discount_type", "discount_value", "min_order_amount"]

    def get_image(self, obj):
        return self.media(obj.image)


class FeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeatureHighlight
        fields = ["id", "icon", "title", "text"]


class CollectionSerializer(MediaUrlMixin, serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Collection
        fields = ["id", "name", "slug", "eyebrow", "description", "image", "product_count"]

    def get_image(self, obj):
        return self.media(obj.image)

    def get_product_count(self, obj):
        count = getattr(obj, "active_count", None)
        return count if count is not None else obj.products.filter(is_active=True).count()


class ProductCardSerializer(MediaUrlMixin, serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    hover_image = serializers.SerializerMethodField()
    colours = ColourSerializer(many=True, read_only=True)
    sizes = SizeSerializer(many=True, read_only=True)
    savings = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)
    colour_images = serializers.SerializerMethodField()
    default_colour = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "name", "slug", "short_description", "price", "compare_at_price", "savings", "badge_text",
            "fit", "image", "hover_image", "colours", "sizes", "is_new_arrival", "is_bestseller", "in_stock",
            "colour_images", "default_colour",
        ]

    def get_colour_images(self, obj):
        """First photo for each colour, so cards can swap the image when a swatch is picked."""
        result = {}
        for img in self._images(obj):
            if img.colour_id and img.colour.name not in result:
                result[img.colour.name] = self.media(img.image)
        return result

    def get_default_colour(self, obj):
        images = self._images(obj)
        if images and images[0].colour_id:
            return images[0].colour.name
        colours = list(obj.colours.all())
        return colours[0].name if colours else ""

    def _images(self, obj):
        return list(obj.images.all())  # uses prefetch

    def get_image(self, obj):
        images = self._images(obj)
        return self.media(images[0].image) if images else None

    def get_hover_image(self, obj):
        images = self._images(obj)
        return self.media(images[1].image) if len(images) > 1 else None


class ProductDetailSerializer(ProductCardSerializer):
    images = serializers.SerializerMethodField()
    collections = serializers.SerializerMethodField()

    class Meta(ProductCardSerializer.Meta):
        fields = ProductCardSerializer.Meta.fields + ["description", "fabric", "images", "collections"]

    def get_images(self, obj):
        return [
            {"url": self.media(img.image), "alt": img.alt_text or obj.name, "colour": img.colour.name if img.colour else None}
            for img in obj.images.all()
        ]

    def get_collections(self, obj):
        return [{"name": c.name, "slug": c.slug} for c in obj.collections.filter(is_active=True)]


class TeeStyleSerializer(serializers.ModelSerializer):
    colours = ColourSerializer(many=True, read_only=True)
    sizes = SizeSerializer(many=True, read_only=True)

    class Meta:
        model = TeeStyle
        fields = ["id", "name", "description", "base_price", "front_print_price", "back_print_price", "colours", "sizes"]


def _check_image(file, label):
    if not file:
        return
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    if file.size > limit:
        raise serializers.ValidationError({label: f"Keep the image under {settings.MAX_UPLOAD_MB} MB."})
    try:
        with Image.open(file) as img:
            img.verify()
            if img.format not in {"JPEG", "PNG", "WEBP"}:
                raise serializers.ValidationError({label: "Upload a JPG, PNG or WEBP image."})
    except (UnidentifiedImageError, OSError):
        raise serializers.ValidationError({label: "That file is not a readable image."})
    finally:
        file.seek(0)


class CustomDesignCreateSerializer(serializers.ModelSerializer):
    tee_style = serializers.PrimaryKeyRelatedField(queryset=TeeStyle.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1, max_value=50, default=1)

    class Meta:
        model = CustomDesignRequest
        fields = [
            "tee_style", "colour", "size", "placement", "quantity", "front_artwork", "back_artwork",
            "front_mockup", "back_mockup", "notes", "customer_name", "customer_email", "customer_phone",
        ]
        extra_kwargs = {"notes": {"required": False, "max_length": 1000}}

    def validate_customer_phone(self, value):
        digits = "".join(ch for ch in value if ch.isdigit())
        if not 10 <= len(digits) <= 13:
            raise serializers.ValidationError("Enter a valid phone number.")
        return digits

    def validate(self, attrs):
        style, placement = attrs["tee_style"], attrs["placement"]
        if not style.colours.filter(pk=attrs["colour"].pk).exists():
            raise serializers.ValidationError({"colour": "This colour is not available for the chosen style."})
        if not style.sizes.filter(pk=attrs["size"].pk).exists():
            raise serializers.ValidationError({"size": "This size is not available for the chosen style."})
        if placement in (CustomDesignRequest.FRONT, CustomDesignRequest.BOTH) and not attrs.get("front_artwork"):
            raise serializers.ValidationError({"front_artwork": "Upload artwork for the front."})
        if placement in (CustomDesignRequest.BACK, CustomDesignRequest.BOTH) and not attrs.get("back_artwork"):
            raise serializers.ValidationError({"back_artwork": "Upload artwork for the back."})
        # Drop files for a side that will not be printed.
        if placement == CustomDesignRequest.FRONT:
            attrs.pop("back_artwork", None)
            attrs.pop("back_mockup", None)
        if placement == CustomDesignRequest.BACK:
            attrs.pop("front_artwork", None)
            attrs.pop("front_mockup", None)
        for name in ("front_artwork", "back_artwork", "front_mockup", "back_mockup"):
            _check_image(attrs.get(name), name)
        attrs["estimated_price"] = style.price_for(placement)
        return attrs


class CustomDesignStatusSerializer(MediaUrlMixin, serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    placement_label = serializers.CharField(source="get_placement_display", read_only=True)
    tee_style = serializers.CharField(source="tee_style.name")
    colour = ColourSerializer()
    size = serializers.CharField(source="size.name")
    unit_price = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)
    front_mockup = serializers.SerializerMethodField()
    back_mockup = serializers.SerializerMethodField()
    can_order = serializers.SerializerMethodField()

    class Meta:
        model = CustomDesignRequest
        fields = [
            "token", "reference", "status", "status_label", "tee_style", "colour", "size", "placement",
            "placement_label", "quantity", "unit_price", "admin_note", "front_mockup", "back_mockup",
            "can_order", "created_at", "reviewed_at",
        ]

    def get_front_mockup(self, obj):
        return self.media(obj.front_mockup or obj.front_artwork)

    def get_back_mockup(self, obj):
        return self.media(obj.back_mockup or obj.back_artwork)

    def get_can_order(self, obj):
        return obj.status == CustomDesignRequest.APPROVED


class CartLineSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["product", "custom"], default="product")
    slug = serializers.CharField(required=False, allow_blank=True)
    colour = serializers.CharField(required=False, allow_blank=True)
    size = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.IntegerField(required=False, default=1)
    token = serializers.CharField(required=False, allow_blank=True)


class QuoteSerializer(serializers.Serializer):
    items = CartLineSerializer(many=True)
    coupon_code = serializers.CharField(required=False, allow_blank=True, max_length=30)


class OrderCreateSerializer(QuoteSerializer):
    customer_name = serializers.CharField(max_length=100)
    customer_email = serializers.EmailField()
    customer_phone = serializers.CharField(max_length=15)
    address_line1 = serializers.CharField(max_length=200)
    address_line2 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=80)
    state = serializers.CharField(max_length=80)
    pincode = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter a 6 digit PIN code."})
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)
    payment_method = serializers.ChoiceField(choices=[c[0] for c in Order.PAYMENT_METHODS], default="cod")

    def validate_customer_phone(self, value):
        digits = "".join(ch for ch in value if ch.isdigit())
        if not 10 <= len(digits) <= 13:
            raise serializers.ValidationError("Enter a valid phone number.")
        return digits


class OrderSummarySerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["number", "status", "subtotal", "discount", "shipping", "total", "payment_method", "created_at", "items"]

    def get_items(self, obj):
        return [
            {"name": i.name, "colour": i.colour, "size": i.size, "quantity": i.quantity, "line_total": str(i.line_total)}
            for i in obj.items.all()
        ]
