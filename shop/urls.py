from django.urls import path

from . import views

urlpatterns = [
    path("site/", views.site_view, name="site"),
    path("home/", views.home_view, name="home"),
    path("collections/", views.collection_list_view, name="collections"),
    path("collections/<slug:slug>/", views.collection_detail_view, name="collection-detail"),
    path("products/", views.product_list_view, name="products"),
    path("products/<slug:slug>/", views.product_detail_view, name="product-detail"),
    path("customizer/", views.customizer_view, name="customizer"),
    path("custom-designs/", views.custom_design_create_view, name="custom-design-create"),
    path("custom-designs/<uuid:token>/", views.custom_design_status_view, name="custom-design-status"),
    path("cart/quote/", views.quote_view, name="cart-quote"),
    path("orders/", views.order_create_view, name="order-create"),
]
