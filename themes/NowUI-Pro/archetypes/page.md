---
authors:
  - bruno-amaral
date: {{ .Date }}
description: ""
draft: false
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
title: "{{ replace (getenv "SLUG") "-" " " | title }}"
layout: page
options:
  unlisted: false
  showHeader: true
  hideFooter: false
  hideSubscribeForm: false
  header:
scripts:
  -
---
