---
title: "Developers"
subtitle: "There is an API to query the MS Database that you can use, for free."
date: 2021-08-11T15:27:16+01:00
lastmod: 
author: Bruno Amaral
options:
  unlisted: false
  header: mini

description: 
categories: []
tags: []

url: api

menu:
  main:
    Name: Developers
    Weight: 5

draft: false
enableDisqus : true
enableMathJax: false
disableToC: false
disableAutoCollapse: true

resources:
  - src: lagos-techie-kwzWjTnDPLk-unsplash.jpeg
    name: header
---

## API Endpoints

### Articles

`https://api.gregory-ms.com/articles/all` : Lists all articles.

`https://api.gregory-ms.com/articles/by-date/{year}/{month}` : List articles for specified {year} and {month}. 

`https://api.gregory-ms.com/articles/id/{ID}` : List article that matches the {ID} number.

`https://api.gregory-ms.com/articles/keyword/{keyword}` : List all articles by keyword.

`https://api.gregory-ms.com/articles/relevant` : List all relevant articles.

#### Articles' Sources

`https://api.gregory-ms.com/articles/source/{source}` : List all articles from specified {source}.

`https://api.gregory-ms.com/articles/sources` : List all available sources.

### Trials

`https://api.gregory-ms.com/trials/all` : List all trials.

`https://api.gregory-ms.com/trials/keyword/{keyword}` : List all trials by keyword.

#### Trials' Sources

`https://api.gregory-ms.com/trials/source/{source}` : List all trials from specified {source}.

`https://api.gregory-ms.com/trials/sources` : List all available sources.

## Database strucuture

### Articles

The JSON response contains information on scientific articles retrieved from multiple academic sources, with the following information for each article:

- **article_id** - The ID of the article
- **discovery_date** - The date this record was retrieved from its source
- **title** - The title of the article
- **summary** - The abstract or summary of the article
- **link** - The link from which the article was retrieved
- **published_date** - The date it was published
- **source** - The source from which the article was retrieved
- **relevant** - Whether this article is relevant or not (manually tagged by a human)
- **table_constraints** - created automatically by SQLite
- **sent** - A binary value that indicates if the article was sent to the admin. (The admin receives an email digest every 48 hours with the listings to mark them as relevant or not)