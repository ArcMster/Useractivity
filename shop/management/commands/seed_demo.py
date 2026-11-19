"""Fill the store with demo content: python manage.py seed_demo [--reset]

Product photos are drawn with Pillow, so the demo needs no external images.
Replace them with real photos from Django admin whenever you are ready.
"""
import io
import random
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from shop.models import (
    AnnouncementMessage,
    Collection,
    CollectionItem,
    Colour,
    CustomDesignRequest,
    FeatureHighlight,
    Offer,
    Order,
    Product,
    ProductImage,
    SiteSettings,
    Size,
    Spotlight,
    SpotlightItem,
    TeeStyle,
)

# Same outline as the browser customizer (frontend/js/tee.js), in 0..1 units.
TEE_OUTLINE = [
    (0.30, 0.145), (0.42, 0.12), (0.445, 0.155), (0.5, 0.17), (0.555, 0.155), (0.58, 0.12),
    (0.70, 0.145), (0.88, 0.24), (0.96, 0.42), (0.83, 0.47), (0.80, 0.36), (0.80, 0.84),
    (0.20, 0.84), (0.20, 0.36), (0.17, 0.47), (0.04, 0.42), (0.12, 0.24),
]
NECK = [(0.42, 0.12), (0.445, 0.155), (0.5, 0.17), (0.555, 0.155), (0.58, 0.12)]

# Kerala inspired tee colours.
COLOURS = [
    ("Jet Black", "#151A18"), ("Kasavu Cream", "#F1EADA"), ("Palm Green", "#2E5B3B"), ("Laterite", "#9E4426"),
    ("Backwater Teal", "#1E5560"), ("Coir", "#C4A27A"), ("Lime Shoot", "#B8E84F"), ("Monsoon Grey", "#6A7472"),
]
LIME = (184, 232, 79)
LATERITE = (224, 102, 42)
ZARI = (214, 178, 94)
SIZES = [("S", "38"), ("M", "40"), ("L", "42"), ("XL", "44"), ("XXL", "46")]

COLLECTIONS = [
    ("Kerala Culture", "Rooted in God's own country", "Chenda beats, backwaters and temple art, reworked as streetwear."),
    ("Retro Cinema", "For the frame-by-frame crowd", "Old theatre posters, ticket stubs and interval punches."),
    ("Minimal Basics", "Everyday heavyweights", "Clean fronts, small chest prints, all heavy cotton."),
    ("Acid Wash", "Washed, faded, lived in", "Hand acid washed tees with a vintage finish."),
]

PRODUCTS = [
    # name, collection, price, compare, colours, graphic word, flags
    ("Chenda Beat", "Kerala Culture", 699, 899, ["Jet Black", "Kasavu Cream", "Laterite"], "CHENDA", {"new": True, "best": True, "badge": "Bestseller"}),
    ("Backwater Drift", "Kerala Culture", 649, 849, ["Palm Green", "Kasavu Cream"], "DRIFT", {"new": True}),
    ("Theyyam Fire", "Kerala Culture", 749, 999, ["Jet Black", "Laterite"], "THEYYAM", {"best": True, "badge": "Limited"}),
    ("Monsoon Mood", "Kerala Culture", 649, None, ["Backwater Teal", "Kasavu Cream", "Lime Shoot"], "MONSOON", {}),
    ("Filter Coffee Club", "Kerala Culture", 599, 799, ["Coir", "Jet Black"], "KAAPI", {"new": True}),
    ("Interval Punch", "Retro Cinema", 699, 899, ["Jet Black", "Kasavu Cream"], "INTERVAL", {"new": True, "best": True}),
    ("Balcony Ticket", "Retro Cinema", 649, 849, ["Laterite", "Coir"], "BALCONY", {"best": True}),
    ("Retro Reel", "Retro Cinema", 699, None, ["Jet Black", "Backwater Teal"], "REEL", {}),
    ("Second Show", "Retro Cinema", 649, 799, ["Lime Shoot", "Jet Black"], "2ND SHOW", {"new": True}),
    ("Just Heavy", "Minimal Basics", 499, 599, ["Jet Black", "Kasavu Cream", "Backwater Teal", "Palm Green"], "AV", {"best": True}),
    ("Small Logo Tee", "Minimal Basics", 449, None, ["Kasavu Cream", "Coir", "Lime Shoot"], "av.", {}),
    ("Acid Static", "Acid Wash", 799, 999, ["Monsoon Grey", "Jet Black"], "STATIC", {"new": True, "badge": "New"}),
    ("Faded Signal", "Acid Wash", 799, None, ["Monsoon Grey", "Laterite"], "SIGNAL", {"best": True}),
]

ANNOUNCEMENTS = [
    ("Free shipping on orders above Rs 999", "bar", "collection.html"),
    ("Use code AVFIRST for 10% off your first order", "bar", ""),
    ("7 day size exchange on every order", "bar", ""),
    ("240 GSM heavyweight cotton", "marquee", ""),
    ("Printed in Kerala", "marquee", ""),
    ("New drops every Friday", "marquee", ""),
    ("Design your own tee", "marquee", "customize.html"),
    ("Cash on delivery available", "marquee", ""),
]

FEATURES = [
    ("◼", "240 GSM heavyweight", "Thick, structured cotton that keeps its shape wash after wash."),
    ("✦", "Prints that last", "DTF and screen prints tested for 50+ washes without cracking."),
    ("➜", "3 to 5 day delivery", "Dispatched from Kochi within 48 hours, tracked to your door."),
    ("↺", "Easy size exchange", "Wrong size? Swap it within 7 days, no questions asked."),
]


def font(size, bold=True):
    names = ["DejaVuSans-Bold.ttf", "DejaVuSansCondensed-Bold.ttf"] if bold else ["DejaVuSans.ttf"]
    for name in names:
        for folder in ["", "/usr/share/fonts/truetype/dejavu/"]:
            try:
                return ImageFont.truetype(folder + name, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def is_dark(rgb):
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) < 140


def shade(rgb, factor):
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)


def draw_tee(colour_hex, word, back=False, bg=(239, 232, 214), size=(800, 1000), accent=None):
    """Return PNG bytes of a flat-lay tee with a simple graphic."""
    scale = 2
    W, H = size[0] * scale, size[1] * scale
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    rgb = hex_rgb(colour_hex)
    pts = [(x * W, y * H) for x, y in TEE_OUTLINE]

    # soft shadow
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).polygon([(x + 18, y + 26) for x, y in pts], fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    img.paste(shade(bg, 0.8), mask=shadow)

    draw.polygon(pts, fill=rgb)
    # folds and seams
    line = shade(rgb, 0.82 if not is_dark(rgb) else 1.35)
    draw.line([(0.20 * W, 0.36 * H), (0.28 * W, 0.16 * H)], fill=line, width=4)
    draw.line([(0.80 * W, 0.36 * H), (0.72 * W, 0.16 * H)], fill=line, width=4)
    draw.line([(0.20 * W, 0.81 * H), (0.80 * W, 0.81 * H)], fill=line, width=3)
    neck = [(x * W, y * H) for x, y in NECK]
    if back:
        neck = [(x * W, (y - 0.025 if 0.44 < x < 0.56 else y) * H) for x, y in NECK]
    draw.line(neck, fill=shade(rgb, 0.7 if not is_dark(rgb) else 1.6), width=14, joint="curve")

    ink = accent or ((245, 240, 226) if is_dark(rgb) else (11, 31, 26))
    pop = LIME if is_dark(rgb) else LATERITE
    cx = 0.5 * W
    if back:
        box = (0.32 * W, 0.25 * H, 0.68 * W, 0.66 * H)
        draw.rounded_rectangle(box, radius=30, outline=ink, width=10)
        draw.text((cx, 0.33 * H), "AV", font=font(int(0.06 * W)), fill=pop, anchor="mm")
        f2 = font(max(24, int(0.62 * W / max(len(word), 4) * 0.42)))
        draw.text((cx, 0.46 * H), word, font=f2, fill=ink, anchor="mm")
        draw.text((cx, 0.58 * H), "EST. KERALA", font=font(int(0.024 * W)), fill=ink, anchor="mm")
    elif word.lower() in ("av", "av."):
        draw.text((0.62 * W, 0.27 * H), word, font=font(int(0.055 * W)), fill=ink, anchor="mm")
    else:
        r = 0.12 * W
        draw.ellipse((cx - r, 0.36 * H - r, cx + r, 0.36 * H + r), fill=pop)
        f = font(max(28, int(0.40 * W / max(len(word), 4) * 1.15)))
        draw.text((cx, 0.53 * H), word, font=f, fill=ink, anchor="mm")
        draw.text((cx, 0.60 * H), "AV APPARELS", font=font(int(0.022 * W)), fill=ink, anchor="mm")

    img = img.resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True)
    return buf.getvalue()


def draw_studio(colour_hex, word, size=(600, 800)):
    """Moody studio shot for spotlight cards: warm lamp light on a dark backdrop."""
    W, H = size
    img = Image.new("RGB", size, (4, 17, 13))
    glow = Image.new("L", size, 0)
    ImageDraw.Draw(glow).ellipse((-W * 0.25, -H * 0.35, W * 1.25, H * 0.55), fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(W * 0.18))
    img.paste((44, 92, 66), mask=glow)
    floor = Image.new("L", size, 0)
    ImageDraw.Draw(floor).ellipse((W * 0.1, H * 0.80, W * 0.9, H * 1.02), fill=150)
    img.paste((92, 80, 40), mask=floor.filter(ImageFilter.GaussianBlur(W * 0.06)))
    tw, th = int(W * 0.98), int(W * 0.98 * 1.25)
    tee = Image.open(io.BytesIO(draw_tee(colour_hex, word, back=False, bg=(20, 44, 34), size=(tw, th))))
    mask = Image.new("L", (tw, th), 0)
    ImageDraw.Draw(mask).polygon([(x * tw, y * th) for x, y in TEE_OUTLINE], fill=255)
    img.paste(tee, ((W - tw) // 2, int(H * 0.08)), mask)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True)
    return buf.getvalue()


def draw_banner(colours, text, size=(1200, 700)):
    W, H = size
    img = Image.new("RGB", size, (11, 31, 26))
    draw = ImageDraw.Draw(img)
    for i in range(H):
        t = i / H
        draw.line([(0, i), (W, i)], fill=(int(11 + 8 * t), int(31 + 20 * t), int(26 + 12 * t)))
    positions = [(0.06, 0.12), (0.34, 0.0), (0.62, 0.12)]
    for (x, y), colour in zip(positions, colours):
        tee = Image.open(io.BytesIO(draw_tee(colour, text, bg=(16, 40, 33), size=(380, 475))))
        mask = Image.new("L", tee.size, 0)
        ImageDraw.Draw(mask).polygon([(px * 380, py * 475) for px, py in TEE_OUTLINE], fill=255)
        img.paste(tee, (int(x * W), int(y * H) + 110), mask)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue()


class Command(BaseCommand):
    help = "Create demo collections, products, offers, running messages and tee styles."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing catalogue and orders first.")

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(7)
        if options["reset"]:
            Order.objects.all().delete()
            CustomDesignRequest.objects.all().delete()
            for model in (SpotlightItem, Spotlight, ProductImage, CollectionItem, Product, Collection, TeeStyle, Offer, AnnouncementMessage, FeatureHighlight):
                model.objects.all().delete()
            self.stdout.write("Cleared existing demo data.")

        if Product.objects.exists():
            self.stdout.write(self.style.WARNING("Products already exist. Run with --reset to rebuild the demo."))
            return

        colours = {}
        for i, (name, hex_code) in enumerate(COLOURS):
            colours[name], _ = Colour.objects.update_or_create(name=name, defaults={"hex_code": hex_code, "sort_order": (i + 1) * 10})
        sizes = {}
        for i, (name, chest) in enumerate(SIZES):
            sizes[name], _ = Size.objects.update_or_create(name=name, defaults={"chest_inches": chest, "sort_order": (i + 1) * 10})

        site = SiteSettings.load()
        site.whatsapp_number = site.whatsapp_number or "919999999999"
        site.contact_email = site.contact_email or "hello@avapparels.in"
        site.instagram_url = site.instagram_url or "https://instagram.com/"
        if not site.hero_image or not site.hero_image.storage.exists(site.hero_image.name):
            site.hero_image.save("hero.jpg", ContentFile(draw_banner(["#151A18", "#F1EADA", "#2E5B3B"], "AV")), save=False)
        site.save()

        tile_colour = {"Kerala Culture": "#2E5B3B", "Retro Cinema": "#9E4426", "Minimal Basics": "#F1EADA", "Acid Wash": "#6A7472"}
        collections = {}
        for i, (name, eyebrow, description) in enumerate(COLLECTIONS):
            c = Collection.objects.create(name=name, eyebrow=eyebrow, description=description, sort_order=(i + 1) * 10)
            c.image.save(f"{c.slug}.jpg", ContentFile(draw_tee(tile_colour[name], name.split()[0].upper(), bg=(14, 38, 31))), save=True)
            collections[name] = c

        for i, (name, coll, price, compare, colour_names, word, flags) in enumerate(PRODUCTS):
            p = Product.objects.create(
                name=name,
                short_description=f"{'Acid washed' if coll == 'Acid Wash' else 'Oversized'} heavyweight tee",
                description=(
                    f"{name} is printed on our 240 GSM heavyweight cotton with a relaxed, dropped shoulder fit. "
                    "Pre-shrunk, bio-washed and made to be worn hard. Machine wash cold, inside out."
                ),
                price=Decimal(price),
                compare_at_price=Decimal(compare) if compare else None,
                fit="Regular" if coll == "Minimal Basics" and "Logo" in name else "Oversized",
                fabric="240 GSM, 100% combed cotton",
                badge_text=flags.get("badge", ""),
                is_new_arrival=flags.get("new", False),
                is_bestseller=flags.get("best", False),
                sort_order=(i + 1) * 10,
            )
            p.colours.set([colours[n] for n in colour_names])
            p.sizes.set(sizes.values())
            CollectionItem.objects.create(collection=collections[coll], product=p, sort_order=(len(collections[coll].items.all()) + 1) * 10)
            for j, colour_name in enumerate(colour_names[:2]):
                front = draw_tee(colours[colour_name].hex_code, word)
                ProductImage.objects.create(
                    product=p, colour=colours[colour_name], alt_text=f"{name} in {colour_name}", sort_order=j * 20 + 10,
                    image=ContentFile(front, name=f"{p.slug}-{j}-front.jpg"),
                )
            back = draw_tee(colours[colour_names[0]].hex_code, word, back=True)
            ProductImage.objects.create(product=p, alt_text=f"{name} back", sort_order=15, image=ContentFile(back, name=f"{p.slug}-back.jpg"))

        styles = [
            ("Oversized", "Dropped shoulders, boxy fit, 240 GSM.", 449, list(colours.keys())[:7]),
            ("Regular fit", "Classic fit, 200 GSM cotton.", 349, ["Jet Black", "Kasavu Cream", "Backwater Teal", "Palm Green", "Laterite"]),
            ("Acid wash", "Hand washed vintage finish, oversized.", 549, ["Monsoon Grey", "Jet Black", "Laterite"]),
        ]
        for i, (name, description, base, colour_names) in enumerate(styles):
            style = TeeStyle.objects.create(name=name, description=description, base_price=Decimal(base), sort_order=(i + 1) * 10)
            style.colours.set([colours[n] for n in colour_names])
            style.sizes.set(sizes.values())

        for i, (text, placement, link) in enumerate(ANNOUNCEMENTS):
            AnnouncementMessage.objects.create(text=text, placement=placement, link=link, sort_order=(i + 1) * 10)

        Offer.objects.create(
            title="10% off your first order", subtitle="New here? Take 10% off, up to Rs 200.", badge_text="Code AVFIRST",
            coupon_code="AVFIRST", discount_type=Offer.PERCENT, discount_value=Decimal("10"), max_discount=Decimal("200"),
            link="collection.html", sort_order=10,
        )
        Offer.objects.create(
            title="Combo deal", subtitle="Flat Rs 150 off on orders above Rs 1,499.", badge_text="Code COMBO150",
            coupon_code="COMBO150", discount_type=Offer.FLAT, discount_value=Decimal("150"), min_order_amount=Decimal("1499"),
            link="collection.html", sort_order=20,
        )
        Offer.objects.create(
            title="Design your own", subtitle="Upload your art, we approve it, you wear it.", badge_text="Custom",
            link="customize.html", sort_order=30, ends_at=timezone.now() + timedelta(days=365),
        )

        spot = Spotlight.objects.create(
            name="Training drop (demo)",
            eyebrow="New · Training drop",
            title="Sweat in",
            highlight="God's own tees.",
            description="Heavyweight 240 GSM tees built for leg day, monsoon runs and Onam alike. Breathable, boxy, made in Kerala.",
            ring_text="TRAIN ✦ SWEAT ✦ REPEAT ✦ AV APPARELS ✦ ",
            collection=collections["Kerala Culture"],
            cta_text="Shop the drop",
            hide_main_hero=True,
            accent_colour="#B8E84F",
            pill_colour="#E0662A",
        )
        picks = [("theyyam-fire", "THEYYAM"), ("chenda-beat", "CHENDA"), ("interval-punch", "INTERVAL"),
                 ("backwater-drift", "DRIFT"), ("acid-static", "STATIC"), ("balcony-ticket", "BALCONY")]
        for i, (slug, word) in enumerate(picks):
            product = Product.objects.filter(slug=slug).first()
            if product:
                hex_code = product.colours.order_by("sort_order").last().hex_code if i % 2 else product.colours.order_by("sort_order").first().hex_code
                SpotlightItem.objects.create(
                    spotlight=spot, product=product, sort_order=(i + 1) * 10,
                    image=ContentFile(draw_studio(hex_code, word), name=f"spot-{slug}.jpg"),
                )

        for i, (icon, title, text) in enumerate(FEATURES):
            FeatureHighlight.objects.create(icon=icon, title=title, text=text, sort_order=(i + 1) * 10)

        self.stdout.write(self.style.SUCCESS(
            f"Demo ready: {Collection.objects.count()} collections, {Product.objects.count()} products, "
            f"{TeeStyle.objects.count()} tee styles, {Offer.objects.count()} offers."
        ))
