---
date: {{ .Date }}
description: ""
draft: false
resources: 
- src: images/
  name: "avatar"
- src: images/
  name: "header"

slug:
subtitle: 
 
name: "{{ replace .Name "-" " " | title }}"
title: "required for SEO on <title>"
options:
  unlisted: false
  showHeader: true
  hideFooter: false
  hideSubscribeForm: false
  header:
scripts:
  -
---
