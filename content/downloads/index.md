---
authors:
  - bruno-amaral
date: 2021-09-22T22:04:46+01:00
description: ""
draft: false
resources: 
- src: images/pexels-vojtech-okenka-392018.jpeg
  name: "header"
- src: "gallery/*.jpg"
  name: gallery-:counter
  title: gallery-title-:counter
- src:
  name: slide-1
slug:
subtitle: Here you can download the full database of articles and clinical trials
tags: 
  - 
categories: 
  - 
title: "Downloads"
layout: only-header
menu:
  main:
    Name: Downloads
    Weight: 3
options:
  unlisted: false
  showHeader: true
  hideFooter: false
  hideSubscribeForm: false
  header: full
scripts:
cta:
  - label: <i class="fas fa-file-excel mr-1"></i> Articles in Excel
    url: /api/articles.xlsx
    classes: btn bg-gradient-success 
  - label: <i class="fas fa-file-excel mr-1"></i> Clinical Trials in Excel
    url: /api/trials.xlsx
    classes: btn btn-success
  - label: <i class="fas fa-file-code mr-1"></i> Articles in JSON
    url: /api/articles.json
    classes: btn-warning
  - label: <i class="fas fa-file-code mr-1"></i> Clinical Trials in JSON
    url: /api/trials.json
    classes: btn btn-warning
---
