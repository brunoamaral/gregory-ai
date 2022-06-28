---
authors:
  - bruno-amaral
date: 2021-11-16T23:10:55Z
description: ""
draft: true
resources: 
- src: images/michael-dziedzic-XTblNijO9IE-unsplash.jpg
  name: "header"
- src: "gallery/*.jpg"
  name: gallery-:counter
  title: gallery-title-:counter
- src:
  name: slide-1
slug:
layout: page
subtitle: 
tags: 
  - 
categories: 
  - 
title: "Technical details"
menu:
  column_1:
   
options:
  unlisted: false
  showHeader: true
  hideFooter: false
  hideSubscribeForm: false
  header: mini
scripts:
  -
---

<div class="col-8 mx-auto">

### Hardware{.title}

Gregory is running on a [Digital Ocean](https://digitalocean.com) virtual private server with these specs. 

- 2 vCPU
- 4 GB Memory 
- 80 GB Disk 
- Ubuntu 20.04 (LTS) x64

Up to date information on the cost of running the server can be found in the [Annual Reports](https://gregory-ms.com/annual-review/).

<div class="row">
<div class="col-md-12">

### Software{.title}

<p>Gregory’s brain is composed of three elements, <a href="https://nodered.org">Node-RED</a> to run searches and <a href="https://www.djangoproject.com/">Django</a> to run machine learning algorithms, produce the API, send notifications, and other emails.</p>
</div>

</div>

### Content Management Software{.title}

We are using [Hugo](https://gohugo.io/) to generate the static pages that make up the website. 

### Design{.title}

The website's Design and HTML was created by [Creative Tim](https://www.creative-tim.com/) and ported to Hugo by [Bruno Amaral](https://brunoamaral.eu/).

### Third party services{.title}

**Email delivery** and management of mailing list is provided by [Mailgun](https://mailgun.com/), using a flex tier account.

**Monitoring** is done by https://healthchecks.io/

{{< copyright >}}

</div>
