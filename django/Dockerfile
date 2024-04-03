# syntax=docker/dockerfile:1
FROM python:3.11
RUN apt-get update && apt-get install -y libpq-dev

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/code"

WORKDIR /code
COPY requirements.txt /code/

RUN pip install -r requirements.txt