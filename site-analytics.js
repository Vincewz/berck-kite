(function () {
  window.va = window.va || function () {
    (window.vaq = window.vaq || []).push(arguments);
  };

  function track(name, data) {
    if (!name || typeof window.va !== 'function') return;
    try {
      window.va('event', {
        name: name,
        data: data || {},
      });
    } catch (_) {
      // Analytics must never affect the public site.
    }
  }

  window.kiteAnalytics = { track: track };

  document.addEventListener('DOMContentLoaded', function () {
    var video = document.getElementById('podcastVideo');
    var audio = document.getElementById('podcastAudio');

    if (video) {
      video.addEventListener('play', function () {
        track('Podcast video play', { page: location.pathname || '/', src: video.currentSrc || video.src });
      }, { once: true });
      video.addEventListener('ended', function () {
        track('Podcast video complete', { page: location.pathname || '/' });
      });
    }

    if (audio) {
      audio.addEventListener('play', function () {
        track('Podcast audio fallback play', { page: location.pathname || '/', src: audio.currentSrc || audio.src });
      }, { once: true });
    }

    document.addEventListener('click', function (event) {
      var link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
      if (!link) return;
      var href = link.getAttribute('href') || '';
      if (href.indexOf('spot.html') !== -1) {
        track('Navigate Spot Live', { from: location.pathname || '/' });
      }
      if (href.indexOf('skaping.com/berck-sur-mer') !== -1) {
        track('Open Skaping external', { url: href });
      }
    });
  });
})();
