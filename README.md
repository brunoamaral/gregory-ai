# Gregory

Gregory is an AI system that uses Machine Learning and Natural Language Processing to track clinical research and identify papers that improve patient wellbeing.

## Overview

Gregory helps research teams stay up-to-date with the latest scientific publications and clinical trials. It intelligently filters and categorizes research from multiple sources, delivering relevant information through APIs, RSS feeds, and email notifications.

## Key Features

1. **Machine Learning & NLP** - Automatically identifies relevant research
2. **RSS Integration** - Gathers and filters search results from PubMed and other sources
3. **Keyword Filtering** - Excludes irrelevant articles from bioRxiv, PNAS, etc.
4. **Flexible Configuration** - Organize research by subjects, categories, and teams
5. **Email Notifications** - Scheduled digests and alerts for new research
6. **API & RSS Feeds** - Integration with websites and other software solutions
7. **Clinical Trials Tracking** - Monitor and receive alerts for new trials
8. **Author Identification** - Tracks authors and their ORCID identifiers

## Documentation

For detailed information, please refer to our documentation:

- [Installation Guide](docs/installation.md)
- [API Reference](docs/api/index.md)
  - [Team API](docs/team-api.md)
  - [Subject API](docs/subject-api.md)
  - [Source API](docs/source-api.md)
  - [Category API](docs/category-api.md)
  - [Article Search API](docs/article-search-api.md)
  - [Trial Search API](docs/trial-search-api.md)
- [Machine Learning Documentation](docs/ml/index.md)
- [Developer Guide](docs/dev/index.md)
- [Deployment Guide](docs/deployment/index.md)

## Current Usage

Gregory is currently used to track research in Multiple Sclerosis at [gregory-ms.com](https://gregory-ms.com).

## Quick Start

```bash
# Clone the repository
git clone <repository_url>
cd <repository_directory>

# Set up environment
cp example.env .env
# Edit .env with your configuration

# Start containers
docker compose up -d

# Initialize database
docker exec admin python manage.py makemigrations
docker exec admin python manage.py migrate
```

Visit `http://localhost:8000/admin` to access the admin interface.

## License

This project is licensed under [LICENSE](LICENSE).

## Acknowledgements

Special thanks to all contributors who have helped with the development of Gregory:

- @[Antoniolopes](https://github.com/antoniolopes) - Machine Learning script
- @[Chbm](https://github.com/chbm) - Security improvements
- @[Jneves](https://github.com/jneves) - Build script
- @[Malduarte](https://github.com/malduarte) - Database migration
- @[Melo](https://github.com/melo) - [Hugo](https://github.com/gohugoio/hugo) integration
- @[Nurv](https://github.com/nurv) - [Spacy.io](https://spacy.io/) integration
- @[Rcarmo](https://github.com/rcarmo) - [Node-RED](https://github.com/node-red/node-red) integration

And the **Lobsters** at [One Over Zero](https://github.com/oneoverzero)
