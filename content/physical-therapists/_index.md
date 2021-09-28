---
authors:
  - bruno-amaral
date: 2021-09-23T20:37:23+01:00
description: ""
draft: true


resources: 
- src: images/
  name: "header"
- src: "gallery/*.jpg"
  name: gallery-:counter
  title: gallery-title-:counter
- src:
  name: slide-1
slug:
subtitle: 
tags: 
  - 
categories: 
  - 
title: ""

options:
  unlisted: false
  showHeader: true
  hideFooter: false
  hideSubscribeForm: false
  header: small
scripts:
  -
---

### Listing results from PEDro and Scielo

{{< list-by sources="PEDro Scielo" >}}