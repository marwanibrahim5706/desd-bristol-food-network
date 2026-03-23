from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ProductForm
from .models import Product


def discovery(request):
    q = (request.GET.get("q") or "").strip()
    available = request.GET.get("available")

    qs = Product.objects.select_related("producer").filter(is_active=True)

    if available in ("1", "true", "True", "yes", "on"):
        qs = qs.filter(stock_quantity__gt=0)

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(producer__username__icontains=q)
            | Q(producer__email__icontains=q)
        )

    qs = qs.order_by("-id")

    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "market_products/discovery.html",
        {"page_obj": page_obj, "q": q, "available": available},
    )


def product_detail(request, pk: int):
    product = get_object_or_404(
        Product.objects.select_related("producer"),
        pk=pk,
        is_active=True,
    )
    return render(request, "market_products/product_detail.html", {"product": product})


@login_required
def producer_product_list(request):
    products = (
        Product.objects.filter(producer=request.user)
        .order_by("-id")
    )

    return render(
        request,
        "market_products/producer_product_list.html",
        {"products": products},
    )


@login_required
def producer_product_create(request):
    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.producer = request.user
            product.save()
            return redirect("producer_product_list")
    else:
        form = ProductForm()

    return render(
        request,
        "market_products/producer_product_form.html",
        {
            "form": form,
            "page_title": "Add Product",
            "submit_label": "Create Product",
        },
    )


@login_required
def producer_product_edit(request, pk: int):
    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            return redirect("producer_product_list")
    else:
        form = ProductForm(instance=product)

    return render(
        request,
        "market_products/producer_product_form.html",
        {
            "form": form,
            "page_title": "Edit Product",
            "submit_label": "Save Changes",
            "product": product,
        },
    )