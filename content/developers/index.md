---
title: "Developers"
subtitle: "All you need to access our database"
date: 2021-08-11T15:27:16+01:00
lastmod: 
author: Bruno Amaral
options:
  unlisted: false
  header: mini

description: 
categories: []
tags: []


layout: page

menu:
  main:
    Name: Developers
    Weight: 6

draft: false
enableDisqus : true
enableMathJax: false
disableToC: false
disableAutoCollapse: true

resources:
  - src: lagos-techie-kwzWjTnDPLk-unsplash.jpeg
    name: header
---

<div class="col-md-6 mx-auto">

## RSS{.title .text-primary}

There are RSS feeds you can use for both Articles and Clinical Trials.

<a class="btn btn-outline-primary" href="/articles/index.xml"><i class="fas fa-rss"></i> RSS for Articles</a> <a class="btn btn-outline-primary" href="/trials/index.xml"><i class="fas fa-rss"></i> RSS for Clinical Trials</a>


## API Endpoints{.title .text-primary}


The API is served using Django Rest Framework and can be accessed at <https://api.gregory-ms.com/>. 

### Articles{.title .text-muted}

**List all articles**

`https://api.gregory-ms.com/articles/all?format=json`

Example: <a href="https://api.gregory-ms.com/articles/all?format=json">https://api.gregory-ms.com/articles/all?format=json</a>

**List article that matches the {ID} number.**    

`https://api.gregory-ms.com/articles/id/{ID}`


Example: <a href="https://api.gregory-ms.com/articles/19">https://api.gregory-ms.com/articles/19</a>


**List all relevant articles.**    

These are articles that we show on the home page because they appear to offer new courses of treatment.

`https://api.gregory-ms.com/articles/relevant`

Example: <a href="https://api.gregory-ms.com/articles/relevant">https://api.gregory-ms.com/articles/relevant</a>

#### Articles' Sources{.title .text-muted}

**List all articles from specified {source}.**

`https://api.gregory-ms.com/articles/source/{source_id}/`

Example: <a href="https://api.gregory-ms.com/articles/source/1">https://api.gregory-ms.com/articles/source/1</a>

**List all available sources.**

`https://api.gregory-ms.com/sources/`

Example: <a href="https://api.gregory-ms.com/sources/">https://api.gregory-ms.com/sources/</a>

### Trials{.title .text-muted}

**List all trials.**    

`https://api.gregory-ms.com/trials/all?format=json`

Example: <a href="https://api.gregory-ms.com/trials/all">https://api.gregory-ms.com/trials/all</a>

#### Trials' Sources{.title .text-muted}

**List all trials from specified {source}.**    

`https://api.gregory-ms.com/trials/source/{source_id}`

Example: <a href="https://api.gregory-ms.com/trials/source/12/">https://api.gregory-ms.com/trials/source/12/</a>

## Database Structure{.title .text-primary}

### Articles{.title .text-muted}

The JSON response contains information on scientific articles retrieved from multiple academic sources, with the following information for each article:

- **article_id**: The ID of the article
- **discovery_date**: The date this record was retrieved from its source
- **link**: The link to the original content
- **ml_prediction_gnb**: A value of 0 or 1 if the article is relevant according to a Gaussian Naive Bayes model
- **ml_prediction_lr**: A value of 0 or 1 if the article is relevant according to a Logistic Regression model
- **noun_phrases**: Extraction of _base noun phrases_ from the title of the article. More information on [Spacy.io](https://spacy.io/usage/linguistic-features#noun-chunks).
- **published_date**: The date it was published
- **relevant**: Whether this article is relevant or not (tagged by a human)
- **sent**: A binary value that indicates if the article was sent to the admin. (The admin receives an email digest every 48 hours with the listings to mark them as relevant or not)
- **source**: The source from which the article was retrieved
- **summary**: The abstract or summary of the article
- **table_constraints**: created automatically by SQLite
- **title**: The title of the article

### Trials{.title .text-muted}

- **discovery_date**: The date this record was retrieved from its source
- **link**: The link to the original content
- **published_date**: The date it was published
- **relevant**: Whether it is relevant or not (tagged by a human, and not used at the moment)
- **sent**: A binary value that indicates if the article was sent to the admin. (The admin receives an email digest every 48 hours with the listings to mark them as relevant or not)
- **source**: Website where the found information about this clinical trial
- **summary**: The abstract or summary of the clinical trial
- **table_constraints**: created automatically by SQLite
- **title**: The title of the clinical trial
- **trial_id**: The ID of the clinical trial

</div>