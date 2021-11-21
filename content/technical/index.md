---
authors:
  - bruno-amaral
date: 2021-11-16T23:10:55Z
description: ""
draft: false
resources: 
- src: images/michael-dziedzic-XTblNijO9IE-unsplash.jpeg
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

<div class="col-6 mx-auto">

### Hardware

Gregory is running on a [Digital Ocean](https://digitalocean.com) virtual private server with these specs. 

- 4 GB Memory 
- 80 GB Disk 
- Ubuntu 20.04 (LTS) x64

On November 16<sup>th</sup> this represented the following monthly costs.

<table class="table table-striped">
<thead>
<tr>
<th><strong>Item</strong></th>
<th><strong>Cost per month</strong></th>
</tr>
</thead>
<tr>
<td>Server</td>
<td>$20.00 USD</td>
</tr>
<tbody>
<tr>
<td>Backups</td>
<td>$4.00 USD</td>
</tr>
<tr>
<td><strong>Total</strong></td>
<td>$24.00 USD</td>
</tr>
</tbody>
</table>

### Software{.title}

Gregory’s brain is composed of two elements, [Node-RED](https://nodered.org) to run searches and [Python](https://www.python.org/) to execute the machine learning algorithm. 

![node-red-icon-2](images/node-red-icon-2.svg)

![python-logo-generic](images/python-logo-generic.svg)

### Content Management Software{.title}

We are using Hugo to generate the static pages that make up the website. 

![](images/hugo-logo-wide.svg)


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



The website's HTML was created by [Creative Tim](https://www.creative-tim.com/) and ported to Hugo by [Bruno Amaral](https://brunoamaral.eu/).

![](images/now_ui.jpeg)

<img src="images/logo-ct-white-170d794e447f75aec55c6effdfbedced9dd268ceceece152675ff8f9891e3588.svg" style="color:#000; filter: invert(100%) ;">

### Third party services{.title}



Email delivery and management of mailing list is provided by Mailgun, using a free tier account.

![Mailgun_Secondary](images/Mailgun_Secondary.png)





Monitoring is done by https://healthchecks.io/

![logo-rounded](images/logo-rounded.svg)



{{< copyright >}}


</div>