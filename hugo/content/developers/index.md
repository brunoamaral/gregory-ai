---
title: "Developers"
subtitle: "All you need to access our database"
date: 2021-08-11T15:27:16+01:00
lastmod: 
author: Bruno Amaral
options:
  unlisted: false
  header: mini
description: Gregory MS, information for developers researching multiple sclerosis
categories: []
tags: []
layout: page

menu:
  column_1:
    Name: Developers
    Weight: 3

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

There are two ways you can use this site, through the several subscription options or installing locally for your own goals.

## Installing Gregory MS

[This software is open source and available on GitHub, with instructions to install it using docker images](https://github.com/brunoamaral/gregory#install).

### Hardware requirements{.title}

Gregory is running on a [Digital Ocean](https://digitalocean.com) virtual private server.

- 2 vCPU
- 4 GB Memory 
- 80 GB Disk 
- Ubuntu 20.04 (LTS) x64

### Software requirements{.title}

- Python 3.9
- [Hugo](https://gohugo.io/)


## Access the information{.title}

### RSS{.title .text-primary}

There are RSS a number of RSS feeds you can use to access the database in real time:

<a class="btn btn-outline-primary umami--click--developers-rss-latest-articles" href="https://api.gregory-ms.com/feed/latest/articles/"><i class="fas fa-rss"></i> Latest Articles</a>

<a class="btn btn-outline-primary umami--click--developers-rss-l" href="https://api.gregory-ms.com/feed/latest/trials/"><i class="fas fa-rss"></i> Latest Trials</a>

<a class="btn btn-outline-primary umami--click--developers-rss-latest-trials" href="https://api.gregory-ms.com/feed/machine-learning/"><i class="fas fa-rss"></i> Machine Learning Prediction</a>

### API Endpoints{.title .text-primary}

The API is served using Django Rest Framework and can be accessed at <https://api.gregory-ms.com/>. 

### Articles{.title .text-muted}

**List all articles**

`https://api.gregory-ms.com/articles/all?format=json`

**List article that matches the {ID} number.**    

`https://api.gregory-ms.com/articles/id/{ID}`

Example: <a class="umami--click--developers-api-latest-trials-example" href="https://api.gregory-ms.com/articles/19">https://api.gregory-ms.com/articles/19</a>


**List all relevant articles.**

These are articles that we show on the home page because they appear to offer new courses of treatment.

`https://api.gregory-ms.com/articles/relevant`

#### Articles' Sources{.title .text-muted}

**List all articles from specified {source}.**

`https://api.gregory-ms.com/articles/source/{source_id}/`


**List all available sources.**

`https://api.gregory-ms.com/sources/`

### Trials{.title .text-muted}

**List all trials.**    

`https://api.gregory-ms.com/trials/all?format=json`

Example: <a href="https://api.gregory-ms.com/trials/all">https://api.gregory-ms.com/trials/all</a>

#### Trials' Sources{.title .text-muted}

**List all trials from specified {source}.**    

`https://api.gregory-ms.com/trials/source/{source_id}`

Example: <a class="umami--click--developers-api-all-trials-by-source-example" href="https://api.gregory-ms.com/trials/source/12/">https://api.gregory-ms.com/trials/source/12/</a>

## Database Structure{.title .text-primary}

### Articles{.title .text-muted}

The JSON response contains information on scientific articles retrieved from multiple academic sources.

Available fields can be found at https://api.gregory-ms.com/articles/ by clicking the options button.


### Trials{.title .text-muted}

Data available at https://api.gregory-ms.com/trials/ by clicking the options button.

## Resources

[GitHub repository](https://github.com/brunoamaral/gregory)

[Mobility Report from Apple Watch Data](https://github.com/brunoamaral/mobility-report)

The **Mobility report** is meant to be used as a snapshot of the patient's mobility and walking assymetry using [Apple's HealthKit](https://developer.apple.com/documentation/healthkit) data from the Apple Watch.

</div>