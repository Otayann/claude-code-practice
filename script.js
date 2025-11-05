// =================================
// スクロール時のフェードインアニメーション
// =================================

// Intersection Observer のオプション
const observerOptions = {
    root: null,
    rootMargin: '0px',
    threshold: 0.1
};

// コールバック関数
const observerCallback = (entries, observer) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            // 一度表示されたら監視を解除（パフォーマンス向上）
            observer.unobserve(entry.target);
        }
    });
};

// Observer インスタンスを作成
const fadeInObserver = new IntersectionObserver(observerCallback, observerOptions);

// ページ読み込み時に実行
document.addEventListener('DOMContentLoaded', () => {
    // すべての .fade-in 要素を監視対象に追加
    const fadeInElements = document.querySelectorAll('.fade-in');

    fadeInElements.forEach((element, index) => {
        // 要素ごとに少し遅延を追加（カスケード効果）
        element.style.transitionDelay = `${index * 0.1}s`;
        fadeInObserver.observe(element);
    });

    // FAQアコーディオン機能の初期化
    initFAQ();

    // スムーズスクロールの初期化
    initSmoothScroll();
});


// =================================
// FAQアコーディオン機能
// =================================

function initFAQ() {
    const faqQuestions = document.querySelectorAll('.faq-question');

    faqQuestions.forEach(question => {
        question.addEventListener('click', () => {
            const faqItem = question.parentElement;
            const isActive = faqItem.classList.contains('active');

            // すべてのFAQを閉じる
            document.querySelectorAll('.faq-item').forEach(item => {
                item.classList.remove('active');
            });

            // クリックされたFAQが閉じていた場合のみ開く
            if (!isActive) {
                faqItem.classList.add('active');
            }
        });
    });
}


// =================================
// スムーズスクロール
// =================================

function initSmoothScroll() {
    // ページ内リンクのスムーズスクロール
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const href = this.getAttribute('href');

            // # のみの場合はトップへスクロール
            if (href === '#') {
                e.preventDefault();
                window.scrollTo({
                    top: 0,
                    behavior: 'smooth'
                });
            } else {
                // 対象要素が存在する場合のみスムーズスクロール
                const targetElement = document.querySelector(href);
                if (targetElement) {
                    e.preventDefault();
                    targetElement.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            }
        });
    });
}


// =================================
// ページトップへ戻るボタン（オプション）
// =================================

// スクロール量を監視してページトップボタンを表示/非表示
window.addEventListener('scroll', () => {
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;

    // 200px以上スクロールしたら何かアクションを起こす場合はここに記述
    // 例: ページトップボタンの表示など
});


// =================================
// レスポンシブ対応の補助機能
// =================================

// ウィンドウリサイズ時の処理
let resizeTimer;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
        // リサイズ完了後の処理
        // 必要に応じて要素の再計算などを行う
    }, 250);
});


// =================================
// パフォーマンス最適化
// =================================

// 画像の遅延読み込み（ブラウザがネイティブサポートしている場合）
if ('loading' in HTMLImageElement.prototype) {
    const images = document.querySelectorAll('img[loading="lazy"]');
    images.forEach(img => {
        img.src = img.dataset.src;
    });
} else {
    // Intersection Observer を使用したフォールバック
    const imageObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target;
                img.src = img.dataset.src;
                img.classList.add('loaded');
                observer.unobserve(img);
            }
        });
    });

    const lazyImages = document.querySelectorAll('img[data-src]');
    lazyImages.forEach(img => imageObserver.observe(img));
}


// =================================
// ユーティリティ関数
// =================================

// 要素がビューポート内にあるかチェック
function isInViewport(element) {
    const rect = element.getBoundingClientRect();
    return (
        rect.top >= 0 &&
        rect.left >= 0 &&
        rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
        rect.right <= (window.innerWidth || document.documentElement.clientWidth)
    );
}

// スクロール位置を取得
function getScrollPosition() {
    return {
        x: window.pageXOffset || document.documentElement.scrollLeft,
        y: window.pageYOffset || document.documentElement.scrollTop
    };
}

// デバイスタイプを判定
function getDeviceType() {
    const width = window.innerWidth;
    if (width < 768) return 'mobile';
    if (width < 1024) return 'tablet';
    return 'desktop';
}


// =================================
// アニメーション補助関数
// =================================

// カウントアップアニメーション（数字を使う場合に便利）
function animateValue(element, start, end, duration) {
    let startTimestamp = null;
    const step = (timestamp) => {
        if (!startTimestamp) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);
        element.textContent = Math.floor(progress * (end - start) + start);
        if (progress < 1) {
            window.requestAnimationFrame(step);
        }
    };
    window.requestAnimationFrame(step);
}


// =================================
// エラーハンドリング
// =================================

// グローバルエラーハンドラー
window.addEventListener('error', (event) => {
    console.error('エラーが発生しました:', event.error);
});

// Promise のリジェクトを捕捉
window.addEventListener('unhandledrejection', (event) => {
    console.error('未処理のPromiseリジェクション:', event.reason);
});
