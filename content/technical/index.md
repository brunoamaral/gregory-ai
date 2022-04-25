---
authors:
  - bruno-amaral
date: 2021-11-16T23:10:55Z
description: ""
draft: false
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

- 4 GB Memory 
- 80 GB Disk 
- Ubuntu 20.04 (LTS) x64

On November 16<sup>th</sup> this represented the following costs.

<table class="table table-striped">
<thead>
<tr>
<th><strong>Item</strong></th>
<th><strong>Cost</strong></th>
</tr>
</thead>
<tr><td>Domain cost per year</td>
<td>$8.57	USD</td>
<tr>
<td>Server cost per month</td>
<td>$20.00 USD</td>
</tr>
<tbody>
<tr>
<td>Backups cost per month</td>
<td>$4.00 USD</td>
</tr>
<tr>
<td><strong>Total cost per year</strong></td>
<td class="text-danger font-weight-bold">$296,57 USD</td>
</tr>
</tbody>
</table>


<div class="row">
<div class="col-md-12">


### Software{.title}


<p>Gregory’s brain is composed of two elements, <a href="https://nodered.org">Node-RED</a> to run searches and <a href="https://www.python.org/">Python</a> to execute the machine learning algorithm.</p>
</div>

</div>


### Content Management Software{.title}

We are using [Hugo](https://gohugo.io/) to generate the static pages that make up the website. 


```BashSession
Start building sites … 
hugo v0.88.1-5BC54738+extended linux/amd64 BuildDate=2021-09-04T09:39:19Z VendorInfo=gohugoio

                   |  EN   
-------------------+-------
  Pages            | 6055  
  Paginator pages  | 1465  
  Non-page files   |  205  
  Static files     | 1739  
  Processed images |   42  
  Aliases          |   26  
  Sitemaps         |    1  
  Cleaned          |    0  

Total in 228433 ms
```

### Design{.title}

The website's Design and HTML was created by [Creative Tim](https://www.creative-tim.com/) and ported to Hugo by [Bruno Amaral](https://brunoamaral.eu/).

### Third party services{.title}

**Email delivery** and management of mailing list is provided by [Mailgun](https://mailgun.com/), using a free tier account.


**Monitoring** is done by https://healthchecks.io/



{{< copyright >}}


</div>