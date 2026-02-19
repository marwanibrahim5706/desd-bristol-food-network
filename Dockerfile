# Use Python image
FROM python:3.12

# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Create app folder inside container
WORKDIR /app

# Install dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy project files into container
COPY . /app/

# Run the Django server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]