
Last updated: 2021-05-16T16:37:13+0100

# Quickstart

`cd exampleSite; hugo server --themesDir ../..`


## Asides / Pullquotes

<h3>Aside</h3>

{{< aside align="left" >}}This, in part, explains why podcasts are on the rise. They are produced by journalists and other writers who dive deep into what they are discussing give the listener more than just a skim of the surface. {{< /aside >}}

## Full Width Image

{{< image-fullwidth src="images/example.jpg" class="" >}}

## Image float left 


{{< image-floatleft src="https://placekitten.com/200/300" class="animate animation__fadeInLeft" >}} Lorem ipsum dolor sit amet, consectetur adipisicing elit. Repudiandae illum nesciunt magnam explicabo iure ipsum quae accusamus esse, laboriosam nulla tenetur debitis facere natus cupiditate, rem officia quis, odit cumque. Lorem ipsum dolor sit amet, consectetur adipisicing elit. Impedit assumenda iure aspernatur dolor doloremque delectus voluptas, libero illo, quo. Nostrum, tenetur, amet! Consequatur dolorem quam provident a eaque, doloribus aut!


## Image float right 

{{< image-floatright src="https://placekitten.com/200/300" class="animate animation__fadeInRight" >}}Lorem ipsum dolor sit amet, consectetur adipisicing elit. Repudiandae illum nesciunt magnam explicabo iure ipsum quae accusamus esse, laboriosam nulla tenetur debitis facere natus cupiditate, rem officia quis, odit cumque. Lorem ipsum dolor sit amet, consectetur adipisicing elit. Impedit assumenda iure aspernatur dolor doloremque delectus voluptas, libero illo, quo. Nostrum, tenetur, amet! Consequatur dolorem quam provident a eaque, doloribus aut!

## Side by side images


{{< image-sidebyside src="images/example-1.jpg" class="animate animation__fadeInLeft" >}}

{{< image-sidebyside src="images/example-2.jpg" class="animate animation__fadeInRight" >}}

The animate classes allow the use of animate.css on scroll.

## Panorama

{{< panorama type="equirectangular" image="31138750083_5e3bfa7df6_o.jpg" showControls="true" autoload="true" author="Benjamim" autorotate="1" >}}



## Gallery

{{< gallery folder="gallery" title="Gallery Example" >}}

## Carousel

{{< carousel title="optional" >}}

frontmatter parameters:

```yaml
resources:     
    - src: carousel/slide1.jpg
      name: slide
      title: slide 1 
    - src: carousel/slide2.jpg
      name: slide 
      title: slide 2  
    - src: carousel/slide3.jpg
      name: slide 
      title: slide 3 
```



## YouTube

{{< youtube -HW7nj-GUZY >}}

## iFrame

```
{{< iframe >}}
<iframe src="https://open.spotify.com/embed/user/amaralb/playlist/0wUPj2HPkZGVRAlSMutyMT" frameborder="0" allowtransparency="true" allow="encrypted-media"></iframe>
{{< /iframe >}}
```

## Get Pages

```
</div> <!-- we need to close some divs to make sure this module displays correctly -->
</div>
</div>

<div class="row">
<div class="col-md-8 mx-auto">
{{< blog-6 ref="story/crypto/_index.md" >}}


</div>
</div>
<div class="container"> <!-- reopen your container or container-fluid div -->
```

## Summary Horizontal

{{< card-blog-horizontal align="right" ref="post/2019-07-15-estrategia-e-resolver-problemas/index.md" >}}

{{< card-blog-horizontal align="left" ref="post/2019-07-15-estrategia-e-resolver-problemas/index.md" >}}
