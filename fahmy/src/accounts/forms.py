from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


User = get_user_model()


class BaseRegistrationForm(forms.Form):
    email = forms.EmailField(max_length=254)
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")

        if password and confirm_password and password != confirm_password:
            self.add_error("confirm_password", "Passwords do not match.")

        if password:
            try:
                validate_password(password)
            except ValidationError as exc:
                self.add_error("password", exc)

        return cleaned_data


class ProducerRegistrationForm(BaseRegistrationForm):
    business_name = forms.CharField(max_length=255)
    contact_name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=20)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    postcode = forms.CharField(max_length=20)

    def save(self):
        user = User.objects.create_user(
            username=self.cleaned_data["email"],
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password"],
        )
        user.role = User.Role.PRODUCER
        user.first_name = self.cleaned_data["contact_name"]
        user.business_name = self.cleaned_data["business_name"]
        user.phone = self.cleaned_data["phone"]
        user.address = self.cleaned_data["address"]
        user.postcode = self.cleaned_data["postcode"]
        user.save()
        return user


class CustomerRegistrationForm(BaseRegistrationForm):
    full_name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=20)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    postcode = forms.CharField(max_length=20)

    def save(self):
        user = User.objects.create_user(
            username=self.cleaned_data["email"],
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password"],
        )
        user.role = User.Role.CUSTOMER
        user.first_name = self.cleaned_data["full_name"]
        user.phone = self.cleaned_data["phone"]
        user.address = self.cleaned_data["address"]
        user.postcode = self.cleaned_data["postcode"]
        user.save()
        return user
