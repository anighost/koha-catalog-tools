if($('div.col-12.col-lg-3.order-md-2').text().trim() == ''){
  $('div.col-12.col-lg-3.order-md-2').hide();
  $('.maincontent').css('margin','0px auto');
}

$('#opac-main-search').append('<div id=\'pic-credit\'>photo credit: <a href="https://unsplash.com/photos/7i7NgMk7dCs">Ravi Singh</a></div>');

$('#pic-credit').css('position','absolute');
$('#pic-credit').css('right','40px');
$('#pic-credit').css('bottom','7px');
$('#pic-credit').css('font-size','12px');
$('#pic-credit').css('color','rgba(255,255,255,0.5)');

$('.navbar-brand').attr("href", 'https://library.disharifoundation.org');

$('#coverflow-staff').ready(function() {
 $('.flipster__button').css("top","35%");
});

$(function() {
$( "#tabs" ).tabs();
});
