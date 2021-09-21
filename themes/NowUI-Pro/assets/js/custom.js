window.onload = function() {

    $.fn.isInViewport = function() {
        var elementTop = $(this).offset().top;
        var elementBottom = elementTop + $(this).outerHeight();
        var viewportTop = $(window).scrollTop();
        var viewportBottom = viewportTop + $(window).height() * .6;

        return elementBottom > viewportTop && elementTop < viewportBottom;
    };

    var $elements = $('img.animate');

    $(window).on("resize scroll load ready", function() {
            $elements.each(function(i) {
                    var $el = $elements.eq(i);
                    if ($el.isInViewport() == true && $el.hasClass('animated') == false) {
                        var newClass = $el.attr('class').replace(/animation__/g, 'animate__');
                        $el.addClass(newClass); 
                        $el.addClass("animate__animated");
                        $el.addClass("animate__slow");
                        $el.addClass("animated nowui-visible");
                        
                    }
                })
            }); 

};

// https://github.com/mrroot5/same-elements-height
var Utils = {
    is_empty: function(data) {
        var count = 0, i;

        if (typeof data === 'number') {
            return false;
        }

        if (typeof data === 'boolean') {
            return !data;
        }

        if (data === undefined || data === null) {
            return true;
        }

        if (data.length !== undefined) {
            return data.length === 0;
        }

        for (i in data) {
            if (data.hasOwnProperty(i)) {
                count += 1;
            }
        }

        return count === 0;
    },
    sameElementsHeight: function(selector) {
        try {
            var elements = document.querySelectorAll(selector),
            max = 0,
            i = 0;

            if (this.is_empty(elements)) {
                throw "No matched selector";
            }

            for (i = 0; i < elements.length; i++) {
                if (elements.hasOwnProperty(i)) {
                    if (elements[i].offsetHeight > max) {
                        max = elements[i].offsetHeight;
                    }
                }
            }

            for (i = 0; i < elements.length; i++) {
                if (elements.hasOwnProperty(i)) {
                    if (elements[i].offsetHeight < max) {
                        // console.log("offset height: " + elements[i].offsetHeight);
                        elements[i].style.height = max + "px";
                        elements[i].style.transform = "translate3d(0,0,0)";
                        // console.log("style height: " + elements[i].style.height);
                    }
                }
            }
        } catch (e) {
            // window.console.error("Same Elements Height: " + e.message);
        }
    }
};

// make navigation change color
document.addEventListener('DOMContentLoaded', function(event) {    
    Utils.sameElementsHeight("h5.card-title");
    Utils.sameElementsHeight(".card-story .card-description");
    Utils.sameElementsHeight(".card-showpage");

    var img = document.createElement('img');
    var src = document.querySelector('.page-header-image').style.backgroundImage.slice(4, -1).replace(/"/g, "");
    if (src != "") {
    img.setAttribute('src', src)
    img.addEventListener('load', function() {
        var vibrant = new Vibrant(img);
        var swatches = vibrant.swatches()
        document.querySelector('nav.bg-dynamic').style.cssText = "background-color:" + swatches["Vibrant"].getHex() + ""
    });
    }else{
        document.querySelector('nav.bg-dynamic').classList.add('bg-primary')
        document.querySelector('nav.bg-dynamic').classList.remove('navbar-transparent')
        document.querySelector('nav.bg-dynamic').classList.remove('bg-dynamic')
    }
})

// Plyr https://github.com/sampotts/plyr
if (Plyr){
    const players = Plyr.setup('.player', {blankVideo: '/plyr/blank.mp4', iconUrl: '/plyr/plyr.svg'});
}