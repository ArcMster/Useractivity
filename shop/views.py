from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, throttle_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from . import services
from .models import (
    AnnouncementMessage,
    Collection,
    CollectionItem,
    CustomDesignRequest,
    FeatureHighlight,
    Offer,
    Product,
    ProductImage,
    SiteSettings,
    Spotlight,
    SpotlightItem,
    TeeStyle,
)
from .serializers import (
    AnnouncementSerializer,
    CollectionSerializer,
    CustomDesignCreateSerializer,
    CustomDesignStatusSerializer,
    FeatureSerializer,
    OfferSerializer,
    OrderCreateSerializer,
    OrderSummarySerializer,
    ProductCardSerializer,
    ProductDetailSerializer,
    QuoteSerializer,
    SiteSettingsSerializer,
    TeeStyleSerializer,
)


class SubmissionThrottle(AnonRateThrottle):
    """Stricter limit for design uploads and orders."""

    scope = "submissions"


def product_queryset():
    return (
        Product.objects.filter(is_active=True)
        .prefetch_related(
            "colours",
            "sizes",
            Prefetch("images", queryset=ProductImage.objects.select_related("colour").order_by("sort_order", "id")),
        )
        .order_by("sort_order", "-created_at")
    )


def active_collections():
    return Collection.objects.filter(is_active=True).annotate(
        active_count=Count("products", filter=Q(products__is_active=True), distinct=True)
    ).order_by("sort_order", "name")  # GROUP BY queries ignore Meta.ordering


def products_in_collection(collection, limit=None):
    items = (
        CollectionItem.objects.filter(collection=collection, product__is_active=True)
        .select_related("product")
        .order_by("sort_order", "id")
    )
    ids = list(items.values_list("product_id", flat=True)[: limit or None])
    by_id = {p.id: p for p in product_queryset().filter(id__in=ids)}
    return [by_id[i] for i in ids if i in by_id]


def layout_payload(request):
    """Shared header, footer and running messages used on every page."""
    ctx = {"request": request}
    messages = AnnouncementMessage.objects.filter(AnnouncementMessage.live_q())
    collections = active_collections()
    return {
        "site": SiteSettingsSerializer(SiteSettings.load(), context=ctx).data,
        "announcements": AnnouncementSerializer(messages.filter(placement=AnnouncementMessage.BAR), many=True).data,
        "marquee": AnnouncementSerializer(messages.filter(placement=AnnouncementMessage.MARQUEE), many=True).data,
        "nav_collections": CollectionSerializer(collections.filter(show_in_nav=True), many=True, context=ctx).data,
    }


@api_view(["GET"])
@throttle_classes([AnonRateThrottle])
def site_view(request):
    return Response(layout_payload(request))


@api_view(["GET"])
@throttle_classes([AnonRateThrottle])
def home_view(request):
    ctx = {"request": request}
    payload = layout_payload(request)
    collections = active_collections()
    payload.update(
        {
            "offers": OfferSerializer(Offer.objects.filter(Offer.live_q(), show_on_home=True), many=True, context=ctx).data,
            "spotlight": spotlight_payload(request),
            "features": FeatureSerializer(FeatureHighlight.objects.filter(is_active=True), many=True).data,
            "tiles": CollectionSerializer(collections.filter(show_as_tile=True), many=True, context=ctx).data,
            "new_arrivals": ProductCardSerializer(product_queryset().filter(is_new_arrival=True)[:8], many=True, context=ctx).data,
            "bestsellers": ProductCardSerializer(product_queryset().filter(is_bestseller=True)[:8], many=True, context=ctx).data,
            "sections": [
                {
                    **CollectionSerializer(c, context=ctx).data,
                    "products": ProductCardSerializer(products_in_collection(c, c.home_product_limit), many=True, context=ctx).data,
                }
                for c in collections.filter(show_on_home=True)
            ],
        }
    )
    return Response(payload)


def spotlight_payload(request):
    spot = Spotlight.objects.filter(Spotlight.live_q()).select_related("collection").first()
    if not spot:
        return None
    ctx = {"request": request}
    items = list(spot.items.filter(product__is_active=True).order_by("sort_order", "id"))
    by_id = {p.id: p for p in product_queryset().filter(id__in=[i.product_id for i in items])}
    cards = []
    for item in items:
        product = by_id.get(item.product_id)
        if product:
            card = ProductCardSerializer(product, context=ctx).data
            if item.image:
                card["image"] = request.build_absolute_uri(item.image.url)
            cards.append(card)
    if not cards and spot.collection_id:
        cards = ProductCardSerializer(products_in_collection(spot.collection, 12), many=True, context=ctx).data
    if not cards:
        return None
    return {
        "eyebrow": spot.eyebrow,
        "title": spot.title,
        "highlight": spot.highlight,
        "description": spot.description,
        "ring_text": spot.ring_text,
        "accent_colour": spot.accent_colour,
        "pill_colour": spot.pill_colour,
        "cta_text": spot.cta_text,
        "collection_slug": spot.collection.slug if spot.collection_id and spot.collection.is_active else "",
        "autoplay_seconds": spot.autoplay_seconds,
        "show_stars": spot.show_stars,
        "hide_main_hero": spot.hide_main_hero,
        "cards": cards,
    }


@api_view(["GET"])
def collection_list_view(request):
    return Response(CollectionSerializer(active_collections(), many=True, context={"request": request}).data)


@api_view(["GET"])
def collection_detail_view(request, slug):
    collection = get_object_or_404(active_collections(), slug=slug)
    ctx = {"request": request}
    return Response(
        {
            **CollectionSerializer(collection, context=ctx).data,
            "products": ProductCardSerializer(products_in_collection(collection), many=True, context=ctx).data,
        }
    )


@api_view(["GET"])
def product_list_view(request):
    """All products. Filters: ?tag=new|bestseller, ?q=search, ?collection=slug."""
    qs = product_queryset()
    slug = request.query_params.get("collection")
    if slug:
        collection = get_object_or_404(Collection, slug=slug, is_active=True)
        products = products_in_collection(collection)
    else:
        tag = request.query_params.get("tag")
        if tag == "new":
            qs = qs.filter(is_new_arrival=True)
        elif tag == "bestseller":
            qs = qs.filter(is_bestseller=True)
        query = (request.query_params.get("q") or "").strip()[:60]
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(short_description__icontains=query) | Q(collections__name__icontains=query)).distinct()
        products = list(qs[:200])
    return Response(ProductCardSerializer(products, many=True, context={"request": request}).data)


@api_view(["GET"])
def product_detail_view(request, slug):
    product = get_object_or_404(product_queryset(), slug=slug)
    ctx = {"request": request}
    related = []
    first = product.collections.filter(is_active=True).order_by("sort_order").first()
    if first:
        related = [p for p in products_in_collection(first, 9) if p.pk != product.pk][:8]
    data = ProductDetailSerializer(product, context=ctx).data
    data["related"] = ProductCardSerializer(related, many=True, context=ctx).data
    return Response(data)


@api_view(["GET"])
def customizer_view(request):
    styles = TeeStyle.objects.filter(is_active=True).prefetch_related("colours", "sizes")
    return Response({"styles": TeeStyleSerializer(styles, many=True).data, "max_upload_mb": _max_mb()})


def _max_mb():
    from django.conf import settings

    return settings.MAX_UPLOAD_MB


@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
@throttle_classes([SubmissionThrottle])
def custom_design_create_view(request):
    serializer = CustomDesignCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    design = serializer.save()
    services.notify_design_submitted(design)
    return Response(
        CustomDesignStatusSerializer(design, context={"request": request}).data, status=status.HTTP_201_CREATED
    )


@api_view(["GET"])
def custom_design_status_view(request, token):
    design = get_object_or_404(CustomDesignRequest.objects.select_related("tee_style", "colour", "size"), token=token)
    return Response(CustomDesignStatusSerializer(design, context={"request": request}).data)


@api_view(["POST"])
@parser_classes([JSONParser])
@throttle_classes([AnonRateThrottle])
def quote_view(request):
    serializer = QuoteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    quote = services.price_cart(serializer.validated_data["items"], serializer.validated_data.get("coupon_code", ""))
    return Response(services.public_quote(quote))


@api_view(["POST"])
@parser_classes([JSONParser])
@throttle_classes([SubmissionThrottle])
def order_create_view(request):
    serializer = OrderCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = dict(serializer.validated_data)
    items = data.pop("items")
    coupon = data.pop("coupon_code", "")
    payment_method = data.pop("payment_method", "cod")
    order = services.create_order(data, items, coupon, payment_method)
    return Response(OrderSummarySerializer(order).data, status=status.HTTP_201_CREATED)
