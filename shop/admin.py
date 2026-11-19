from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from . import services
from .models import (
    AnnouncementMessage,
    Collection,
    CollectionItem,
    Colour,
    CustomDesignRequest,
    FeatureHighlight,
    Offer,
    Order,
    OrderItem,
    Product,
    ProductImage,
    SiteSettings,
    Size,
    Spotlight,
    SpotlightItem,
    TeeStyle,
)


def thumb(field, size=60):
    if not field:
        return "-"
    try:
        url = field.url
    except ValueError:
        return "-"
    return format_html(
        '<a href="{}" target="_blank"><img src="{}" style="height:{}px;width:auto;border-radius:6px;object-fit:cover;background:#eee"></a>',
        url, url, size,
    )


@admin.action(description="Renumber sort order (10, 20, 30...) in the current order")
def renumber_sort_order(modeladmin, request, queryset):
    for index, obj in enumerate(queryset.order_by("sort_order", "pk"), start=1):
        type(obj).objects.filter(pk=obj.pk).update(sort_order=index * 10)
    modeladmin.message_user(request, "Sort order renumbered.")


class SortableAdmin(admin.ModelAdmin):
    """Change the 'Sort order' numbers in the list and click Save to reorder."""

    actions = [renumber_sort_order]
    list_per_page = 100


# ---------------------------------------------------------------------------
# Site content
# ---------------------------------------------------------------------------
@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fieldsets = [
        ("Brand", {"fields": ["store_name", "tagline", "footer_about"]}),
        ("Home hero", {"fields": ["hero_eyebrow", "hero_title", "hero_highlight", "hero_subtitle", "hero_image", "hero_preview", "hero_cta_text", "hero_cta_link"]}),
        ("Custom tee section", {"fields": ["custom_section_title", "custom_section_text"]}),
        ("Contact", {"fields": ["whatsapp_number", "instagram_url", "contact_email", "contact_phone"]}),
        ("Shipping", {"fields": ["shipping_fee", "free_shipping_threshold"]}),
    ]
    readonly_fields = ["hero_preview"]

    def hero_preview(self, obj):
        return thumb(obj.hero_image, 120)

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = SiteSettings.load()
        return redirect(reverse("admin:shop_sitesettings_change", args=[obj.pk]))


@admin.register(AnnouncementMessage)
class AnnouncementAdmin(SortableAdmin):
    list_display = ["text", "placement", "is_active", "sort_order", "starts_at", "ends_at", "live_now"]
    list_editable = ["placement", "is_active", "sort_order"]
    list_filter = ["placement", "is_active"]
    search_fields = ["text"]

    @admin.display(boolean=True, description="Live now")
    def live_now(self, obj):
        return obj.is_live()


@admin.register(Offer)
class OfferAdmin(SortableAdmin):
    list_display = ["title", "coupon_code", "discount_label", "show_on_home", "is_active", "sort_order", "ends_at", "live_now"]
    list_editable = ["show_on_home", "is_active", "sort_order"]
    list_filter = ["is_active", "show_on_home", "discount_type"]
    search_fields = ["title", "coupon_code"]
    fieldsets = [
        ("Banner", {"fields": ["title", "subtitle", "badge_text", "image", "link", "show_on_home"]}),
        ("Coupon (optional)", {"fields": ["coupon_code", "discount_type", "discount_value", "min_order_amount", "max_discount"]}),
        ("Schedule", {"fields": ["is_active", "starts_at", "ends_at", "sort_order"]}),
    ]

    @admin.display(description="Discount")
    def discount_label(self, obj):
        if not obj.coupon_code:
            return "Banner only"
        return f"{obj.discount_value:.0f}%" if obj.discount_type == Offer.PERCENT else f"Rs {obj.discount_value:.0f}"

    @admin.display(boolean=True, description="Live now")
    def live_now(self, obj):
        return obj.is_live()


@admin.register(FeatureHighlight)
class FeatureAdmin(SortableAdmin):
    list_display = ["icon", "title", "text", "is_active", "sort_order"]
    list_editable = ["is_active", "sort_order"]


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
@admin.register(Colour)
class ColourAdmin(SortableAdmin):
    list_display = ["swatch", "name", "hex_code", "sort_order"]
    list_display_links = ["name"]
    list_editable = ["hex_code", "sort_order"]

    def swatch(self, obj):
        return format_html('<span style="display:inline-block;width:22px;height:22px;border-radius:50%;background:{};border:1px solid #999"></span>', obj.hex_code)


@admin.register(Size)
class SizeAdmin(SortableAdmin):
    list_display = ["name", "chest_inches", "sort_order"]
    list_editable = ["chest_inches", "sort_order"]


class CollectionItemInline(admin.TabularInline):
    model = CollectionItem
    extra = 0
    fields = ["product", "product_thumb", "sort_order"]
    readonly_fields = ["product_thumb"]
    autocomplete_fields = ["product"]
    ordering = ["sort_order", "id"]

    def product_thumb(self, obj):
        image = obj.product.images.first() if obj.pk else None
        return thumb(image.image if image else None, 48)

    product_thumb.short_description = "Preview"


@admin.register(Collection)
class CollectionAdmin(SortableAdmin):
    list_display = ["preview", "name", "product_count", "is_active", "show_on_home", "show_in_nav", "show_as_tile", "sort_order"]
    list_display_links = ["name"]
    list_editable = ["is_active", "show_on_home", "show_in_nav", "show_as_tile", "sort_order"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [CollectionItemInline]
    fieldsets = [
        (None, {"fields": ["name", "slug", "eyebrow", "description", "image"]}),
        ("Where it appears", {"fields": ["is_active", "show_on_home", "show_in_nav", "show_as_tile", "home_product_limit", "sort_order"]}),
    ]

    def preview(self, obj):
        return thumb(obj.image, 40)

    @admin.display(description="Products")
    def product_count(self, obj):
        return obj.items.count()


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ["image", "preview", "alt_text", "colour", "sort_order"]
    readonly_fields = ["preview"]

    def preview(self, obj):
        return thumb(obj.image, 60)


class ProductCollectionInline(admin.TabularInline):
    model = CollectionItem
    extra = 1
    fields = ["collection", "sort_order"]
    verbose_name = "Collection"
    verbose_name_plural = "Collections this product belongs to"


@admin.register(Product)
class ProductAdmin(SortableAdmin):
    list_display = ["preview", "name", "price", "compare_at_price", "is_active", "is_new_arrival", "is_bestseller", "in_stock", "sort_order"]
    list_display_links = ["name"]
    list_editable = ["price", "is_active", "is_new_arrival", "is_bestseller", "in_stock", "sort_order"]
    list_filter = ["is_active", "is_new_arrival", "is_bestseller", "in_stock", "collections"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    filter_horizontal = ["colours", "sizes"]
    inlines = [ProductImageInline, ProductCollectionInline]
    fieldsets = [
        (None, {"fields": ["name", "slug", "short_description", "description", "badge_text"]}),
        ("Pricing", {"fields": ["price", "compare_at_price"]}),
        ("Options", {"fields": ["fit", "fabric", "colours", "sizes"]}),
        ("Visibility", {"fields": ["is_active", "in_stock", "is_new_arrival", "is_bestseller", "sort_order"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("images")

    def preview(self, obj):
        images = list(obj.images.all())
        return thumb(images[0].image if images else None, 48)


# ---------------------------------------------------------------------------
# Custom tees
# ---------------------------------------------------------------------------
@admin.register(TeeStyle)
class TeeStyleAdmin(SortableAdmin):
    list_display = ["name", "base_price", "front_print_price", "back_print_price", "is_active", "sort_order"]
    list_editable = ["base_price", "front_print_price", "back_print_price", "is_active", "sort_order"]
    filter_horizontal = ["colours", "sizes"]


def _review(modeladmin, request, queryset, new_status, label):
    count = 0
    for design in queryset.exclude(status=CustomDesignRequest.ORDERED):
        design.status = new_status
        design.reviewed_at = timezone.now()
        design.save(update_fields=["status", "reviewed_at", "updated_at"])
        services.notify_design_reviewed(design)
        count += 1
    modeladmin.message_user(request, f"{count} design(s) marked {label}.", messages.SUCCESS)


@admin.action(description="Approve selected designs")
def approve_designs(modeladmin, request, queryset):
    _review(modeladmin, request, queryset, CustomDesignRequest.APPROVED, "approved")


@admin.action(description="Request changes on selected designs")
def request_changes(modeladmin, request, queryset):
    _review(modeladmin, request, queryset, CustomDesignRequest.CHANGES, "changes requested")


@admin.action(description="Reject selected designs")
def reject_designs(modeladmin, request, queryset):
    _review(modeladmin, request, queryset, CustomDesignRequest.REJECTED, "rejected")


@admin.register(CustomDesignRequest)
class CustomDesignAdmin(admin.ModelAdmin):
    list_display = ["reference", "front_preview", "back_preview", "customer_name", "customer_phone", "tee_style", "colour", "size", "placement", "quantity", "status_badge", "created_at"]
    list_filter = ["status", "tee_style", "placement", "created_at"]
    search_fields = ["customer_name", "customer_email", "customer_phone", "token"]
    actions = [approve_designs, request_changes, reject_designs]
    readonly_fields = [
        "reference", "token", "front_preview_large", "back_preview_large", "front_artwork_link", "back_artwork_link",
        "estimated_price", "created_at", "updated_at", "reviewed_at", "status_page",
    ]
    fieldsets = [
        ("Review", {"fields": ["status", "approved_price", "admin_note", "reviewed_at", "status_page"]}),
        ("Design", {"fields": ["reference", "token", "tee_style", "colour", "size", "placement", "quantity", "notes", "estimated_price"]}),
        ("Previews", {"fields": ["front_preview_large", "back_preview_large", "front_artwork_link", "back_artwork_link"]}),
        ("Customer", {"fields": ["customer_name", "customer_email", "customer_phone"]}),
        ("Timestamps", {"fields": ["created_at", "updated_at"]}),
    ]

    def front_preview(self, obj):
        return thumb(obj.front_mockup or obj.front_artwork, 56)

    def back_preview(self, obj):
        return thumb(obj.back_mockup or obj.back_artwork, 56)

    @admin.display(description="Front preview")
    def front_preview_large(self, obj):
        return thumb(obj.front_mockup or obj.front_artwork, 260)

    @admin.display(description="Back preview")
    def back_preview_large(self, obj):
        return thumb(obj.back_mockup or obj.back_artwork, 260)

    @admin.display(description="Front artwork (original file)")
    def front_artwork_link(self, obj):
        return format_html('<a href="{}" target="_blank">Download</a>', obj.front_artwork.url) if obj.front_artwork else "-"

    @admin.display(description="Back artwork (original file)")
    def back_artwork_link(self, obj):
        return format_html('<a href="{}" target="_blank">Download</a>', obj.back_artwork.url) if obj.back_artwork else "-"

    @admin.display(description="Customer status page")
    def status_page(self, obj):
        if not obj.pk:
            return "-"
        url = services.status_link(obj)
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {"pending": "#b7791f", "approved": "#2f855a", "changes_requested": "#2b6cb0", "rejected": "#c53030", "ordered": "#4a5568"}
        return format_html(
            '<span style="background:{};color:#fff;padding:3px 8px;border-radius:10px;font-size:11px">{}</span>',
            colours.get(obj.status, "#4a5568"), obj.get_status_display(),
        )

    def save_model(self, request, obj, form, change):
        status_changed = change and "status" in form.changed_data
        if status_changed and obj.status in (CustomDesignRequest.APPROVED, CustomDesignRequest.CHANGES, CustomDesignRequest.REJECTED):
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)
        if status_changed:
            services.notify_design_reviewed(obj)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ["name", "colour", "size", "quantity", "unit_price", "line_total", "design_link"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def design_link(self, obj):
        if not obj.custom_design_id:
            return "-"
        url = reverse("admin:shop_customdesignrequest_change", args=[obj.custom_design_id])
        return format_html('<a href="{}">{}</a>', url, obj.custom_design.reference)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["number", "customer_name", "customer_phone", "city", "total", "status", "payment_status", "created_at"]
    list_editable = ["status", "payment_status"]
    list_filter = ["status", "payment_status", "payment_method", "created_at"]
    search_fields = ["number", "customer_name", "customer_email", "customer_phone"]
    inlines = [OrderItemInline]
    readonly_fields = ["number", "subtotal", "discount", "shipping", "total", "coupon_code", "created_at"]
    fieldsets = [
        ("Order", {"fields": ["number", "status", "payment_method", "payment_status", "created_at"]}),
        ("Customer", {"fields": ["customer_name", "customer_email", "customer_phone"]}),
        ("Address", {"fields": ["address_line1", "address_line2", "city", "state", "pincode", "notes"]}),
        ("Totals", {"fields": ["subtotal", "coupon_code", "discount", "shipping", "total"]}),
    ]


# ---------------------------------------------------------------------------
# Spotlight carousel
# ---------------------------------------------------------------------------
class SpotlightItemInline(admin.TabularInline):
    model = SpotlightItem
    extra = 1
    fields = ["product", "preview", "image", "sort_order"]
    readonly_fields = ["preview"]
    autocomplete_fields = ["product"]

    def preview(self, obj):
        if not obj.pk:
            return "-"
        if obj.image:
            return thumb(obj.image, 60)
        first = obj.product.images.first()
        return thumb(first.image if first else None, 60)


@admin.register(Spotlight)
class SpotlightAdmin(SortableAdmin):
    list_display = ["name", "title", "card_count", "is_active", "sort_order", "starts_at", "ends_at", "live_now"]
    list_editable = ["is_active", "sort_order"]
    inlines = [SpotlightItemInline]
    fieldsets = [
        (None, {"fields": ["name", "eyebrow", "title", "highlight", "description", "cta_text", "collection"]}),
        ("Look and motion", {"fields": ["accent_colour", "pill_colour", "ring_text", "show_stars", "autoplay_seconds", "hide_main_hero"]}),
        ("Schedule", {"fields": ["is_active", "starts_at", "ends_at", "sort_order"],
                      "description": "The first live spotlight (lowest sort order) shows on the home page."}),
    ]

    @admin.display(description="Cards")
    def card_count(self, obj):
        return obj.items.count()

    @admin.display(boolean=True, description="Live now")
    def live_now(self, obj):
        return obj.is_live()
