if ($('div.col-12.col-lg-3.order-md-2').text().trim() == '') {
  $('div.col-12.col-lg-3.order-md-2').hide();
  $('.maincontent').css('margin', '0px auto');
}

$('#opac-main-search').append('<div id=\'pic-credit\'>photo credit: <a href="https://unsplash.com/photos/7i7NgMk7dCs">Ravi Singh</a></div>');
$('#pic-credit').css('position', 'absolute');
$('#pic-credit').css('right', '40px');
$('#pic-credit').css('bottom', '7px');
$('#pic-credit').css('font-size', '12px');
$('#pic-credit').css('color', 'rgba(255,255,255,0.5)');

$('.navbar-brand').attr('href', 'https://library.disharifoundation.org');

$('#coverflow-staff').ready(function () {
  $('.flipster__button').css('top', '35%');
});

// ── Login modal ──
$(function () {
  var $login = $('#login');
  if ($login.length) {
    var $overlay = $(
      '<div class="dpl-login-overlay" id="dpl-login-modal" role="dialog" aria-modal="true" aria-labelledby="dpl-login-heading">' +
        '<div class="dpl-login-card">' +
          '<button class="dpl-login-close" aria-label="Close">&times;</button>' +
          '<h2 class="dpl-login-title" id="dpl-login-heading">Sign In</h2>' +
        '</div>' +
      '</div>'
    );
    $overlay.find('.dpl-login-card').append($login.detach());
    $('body').append($overlay);

    function openModal() {
      $overlay.addClass('is-open');
      $('body').css('overflow', 'hidden');
      setTimeout(function () { $('#userid').focus(); }, 60);
    }
    function closeModal() {
      $overlay.removeClass('is-open');
      $('body').css('overflow', '');
    }

    // Navbar login link
    $(document).on('click', '#login-link', function (e) {
      e.preventDefault(); openModal();
    });
    // My Account card (only intercept when not logged in)
    $(document).on('click', '.dpl-qcard[href*="opac-user.pl"]', function (e) {
      e.preventDefault(); openModal();
    });
    // Close: × button
    $(document).on('click', '.dpl-login-close', closeModal);
    // Close: backdrop click
    $(document).on('click', '#dpl-login-modal', function (e) {
      if (e.target === this) closeModal();
    });
    // Close: ESC key
    $(document).on('keydown', function (e) {
      if (e.key === 'Escape') closeModal();
    });
  }
});

// ── Shelf tabs (custom — no jQuery UI dependency) ──
$(function () {
  var $nav    = $('#tabs > ul > li');
  var $panels = $('#tabs > div[id^="tabs-"]');

  // Destroy jQuery UI tabs if initialized, to avoid conflicts
  if ($('#tabs').hasClass('ui-tabs')) {
    try { $('#tabs').tabs('destroy'); } catch(e) {}
  }

  $panels.hide();
  $panels.first().show();
  $nav.first().addClass('dpl-tab-active');

  $nav.find('a').on('click', function (e) {
    e.preventDefault();
    var target = $(this).attr('href');
    $panels.hide();
    $(target).show();
    $nav.removeClass('dpl-tab-active');
    $(this).parent().addClass('dpl-tab-active');
  });
});
