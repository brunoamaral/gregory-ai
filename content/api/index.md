---
title: "Developers"
subtitle: "There is an API to query the MS Database that you can use, for free."
subtitle: 
date: 2021-08-11T15:27:16+01:00
lastmod: 
author: Bruno Amaral
options:
  unlisted: false
  header: small

description: 
categories: []
tags: []

url: api

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

## API Endpoints

`https://api.brunoamaral.net/articles/all` : Lists all articles.

`https://api.brunoamaral.net/articles/by-date/{year}/{month}` : List articles for specified {year} and {month}. 

`https://api.brunoamaral.net/articles/id/{ID}` : List article that matches the {ID} number.

`https://api.brunoamaral.net/articles/keyword/{keyword}` : List all articles by keyword.

`https://api.brunoamaral.net/articles/relevant` : List all relevant articles.

`https://api.brunoamaral.net/articles/source/{source}` : List all articles from specified {source}.

`https://api.brunoamaral.net/articles/sources` : List all available sources.

`https://api.brunoamaral.net/trials/keyword/{keyword}` : List all trials by keyword.