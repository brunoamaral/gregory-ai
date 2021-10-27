window.onload = function() {
    // make navigation change color
    var img = document.createElement('img');
    var src = document.querySelector('.page-header-image').style.backgroundImage.slice(4, -1).replace(/"/g, "");
    if (src != "") {
        img.setAttribute('src', src)
        img.addEventListener('load', function() {
            var vibrant = new Vibrant(img);
            var swatches = vibrant.swatches()
            document.querySelector('nav.bg-dynamic').style.cssText = "background-color:" + swatches["Vibrant"].getHex() + ""
        });
    } else {
        document.querySelector('nav.bg-dynamic').classList.add('bg-primary')
        document.querySelector('nav.bg-dynamic').classList.remove('navbar-transparent')
        document.querySelector('nav.bg-dynamic').classList.remove('bg-dynamic')
    }
};