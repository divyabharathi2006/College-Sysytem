(() => {
    const loader = document.getElementById('home-loader');
    const skipButton = document.getElementById('home-loader-skip');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    if (!loader || !skipButton || reducedMotion.matches) {
        return;
    }

    let timer;
    const dismiss = () => {
        window.clearTimeout(timer);
        loader.hidden = true;
        skipButton.removeEventListener('click', dismiss);
    };

    skipButton.addEventListener('click', dismiss);
    loader.hidden = false;
    timer = window.setTimeout(dismiss, 2400);
})();
