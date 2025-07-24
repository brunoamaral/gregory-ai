---
applyTo: "*"
---

# Project Overview

This is a django backend service called GregoryAi, it is designed to fetch data from several sources via rss, api posts, and manual submissions. 

Main features:
- organize and store science papers (articles) and clinical trials (trials) from various sources
- provide a REST API for accessing article data
- main objects in the database: Article, Author, Sources, Subject, Team, Trials
- automated newsletter with latest articles and trials per subject, and categories
- admin email with latest articles and trials per subject to manually review the ML predictions
- Automatic prediction of relevant articles based on Machine Learning models described in `files_repo_PBL_nsbe` folder

## Folder Structure

- `/django`: Contains the Django backend code.
- `files_repo_PBL_nsbe`: Contains documentation and examples for the machine learning models used in the project.
- `/docs`: Contains documentation for the project, including API specifications and user guides.

## Libraries and Frameworks

- Django: The main web framework used for the backend.
- Django REST Framework: Used to build the API endpoints.

## Coding Standards

- use tabs for indentation
- always check django/gregory/models.py when creating new models, adding features, or writing sql queries.


## UI guidelines

- Application should have a modern and clean design.

## Testing

- tests are located in the `tests` folder within the Django app.
- when running commands, assume that the app is running as a docker container called `gregory`.