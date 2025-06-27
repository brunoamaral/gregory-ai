# API Reference

Welcome to the Gregory API reference documentation. Gregory offers a comprehensive API for accessing and managing research articles, clinical trials, teams, subjects, sources, and categories.

## Overview

The Gregory API is built on Django REST Framework and provides both read-only public endpoints and authenticated endpoints for full CRUD operations.

## Authentication

Most GET endpoints are publicly accessible. POST, PUT, PATCH, and DELETE operations require authentication.

Authentication is handled via:
- JWT tokens
- Session-based authentication for browser access
- API key authentication for machine-to-machine communication

## Common Patterns

All Gregory API endpoints follow these common patterns:

- Pagination: Results are paginated with 20 items per page by default
- Ordering: Most collections can be ordered with `?ordering=field` or `?ordering=-field` (descending)
- Filtering: Common filters include `?discovery_date_after=YYYY-MM-DD`
- Error handling: Standard HTTP status codes with descriptive messages

## API Sections

The Gregory API is organized into the following sections:

- [Articles API](articles.md) - Access to scientific publications
- [Trials API](trials.md) - Access to clinical trials data
- [Team API](../team-api.md) - Team-based article and trial management
- [Subject API](../subject-api.md) - Subject-specific article and trial access
- [Source API](../source-api.md) - Source configuration and management
- [Category API](../category-api.md) - Category-based filtering and organization
- [Search API](../article-search-api.md) - Advanced search capabilities

## API Versioning

The current API version is v1. All endpoints are prefixed with `/api/v1/`.

## Rate Limiting

Public API endpoints are rate-limited to 100 requests per hour per IP address. Authenticated requests have higher limits based on the user's role.
