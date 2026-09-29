/**
 * 1000Kitap to Goodreads Exporter - Frontend Kontrolcüsü
 * Vanilla JavaScript (ES6+), Sıfır Bağımlılık, SSE ve Otomatik Fallback Desteği
 */

document.addEventListener('DOMContentLoaded', () => {
  // Canlı Backend API URL'i (GitHub Pages üzerindeyken tünel adresine bağlanır, yereldeyken göreceli çalışır)
  const API_BASE_URL = window.location.hostname.includes('github.io')
    ? 'https://knight-analytical-fork-sessions.trycloudflare.com'
    : '';

  // ============================================================================
  // DOM ELEMENTLERİ
  // ============================================================================
  const liveClock = document.getElementById('liveClock');
  
  // Bölüm Kartları
  const formSection = document.getElementById('formSection');
  const progressSection = document.getElementById('progressSection');
  const completedSection = document.getElementById('completedSection');
  const errorSection = document.getElementById('errorSection');

  // Form Elemanları
  const exportForm = document.getElementById('exportForm');
  const usernameInput = document.getElementById('usernameInput');
  const startBtn = document.getElementById('startBtn');
  const shelfRadios = document.querySelectorAll('input[name="shelf"]');

  function getSelectedShelf() {
    const checked = document.querySelector('input[name="shelf"]:checked');
    return checked ? checked.value : 'hepsi';
  }

  function updateRadioCardStyles() {
    shelfRadios.forEach(radio => {
      const card = radio.closest('.shelf-radio-card');
      if (card) {
        if (radio.checked) {
          card.classList.add('is-selected');
        } else {
          card.classList.remove('is-selected');
        }
      }
    });
  }
  shelfRadios.forEach(radio => {
    radio.addEventListener('change', updateRadioCardStyles);
  });
  updateRadioCardStyles();

  // İncelemeler Checkbox Seçimi
  const includeReviewsCheckbox = document.getElementById('includeReviews');
  function updateCheckboxCardStyle() {
    if (includeReviewsCheckbox) {
      const card = includeReviewsCheckbox.closest('.shelf-checkbox-card');
      if (card) {
        if (includeReviewsCheckbox.checked) {
          card.classList.add('is-selected');
        } else {
          card.classList.remove('is-selected');
        }
      }
    }
  }
  if (includeReviewsCheckbox) {
    includeReviewsCheckbox.addEventListener('change', updateCheckboxCardStyle);
    updateCheckboxCardStyle();
  }

  // İlerleme & Kuyruk Elemanları
  const statusBadge = document.getElementById('statusBadge');
  const statusBadgeText = document.getElementById('statusBadgeText');
  const queueInfoText = document.getElementById('queueInfoText');
  const progressBar = document.getElementById('progressBar');
  const countDisplay = document.getElementById('countDisplay');
  const percentDisplay = document.getElementById('percentDisplay');

  // Canlı Taranan Görsel Kitap Kartı Elemanları
  const liveBookCard = document.getElementById('liveBookCard');
  const liveBookCover = document.getElementById('liveBookCover');
  const liveBookTitle = document.getElementById('liveBookTitle');
  const liveBookAuthor = document.getElementById('liveBookAuthor');
  const bookCoverWrapper = document.getElementById('bookCoverWrapper');
  const bookPlaceholderIcon = document.getElementById('bookPlaceholderIcon');


  // Tamamlanma & İndirme Elemanları
  const finalCount = document.getElementById('finalCount');
  const downloadBtn = document.getElementById('downloadBtn');
  const resetBtn = document.getElementById('resetBtn');
  const previewBtn = document.getElementById('previewBtn');

  // Üst Bar & Durum Elemanları
  const copyLinkBtn = document.getElementById('copyLinkBtn');
  const copyTooltip = document.getElementById('copyTooltip');
  const queueEtaBox = document.getElementById('queueEtaBox');
  const queueEtaText = document.getElementById('queueEtaText');
  const cancelJobBtn = document.getElementById('cancelJobBtn');

  // Önizleme Modalı Elemanları
  const previewModal = document.getElementById('previewModal');
  const closePreviewBtn = document.getElementById('closePreviewBtn');
  const closePreviewFooterBtn = document.getElementById('closePreviewFooterBtn');
  const previewModalBody = document.getElementById('previewModalBody');

  // Hata Elemanları
  const errorMessage = document.getElementById('errorMessage');
  const retryBtn = document.getElementById('retryBtn');
  const privacyHelpBox = document.getElementById('privacyHelpBox');

  // Durum Değişkenleri
  let activeEventSource = null;
  let activePollingInterval = null;
  let currentJobId = null;
  let previewBooks = [];

  // ============================================================================
  // KINDLE SAAT GÜNCELLEYİCİ
  // ============================================================================
  function updateKindleClock() {
    const now = new Date();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    if (liveClock) {
      liveClock.textContent = `${hours}:${minutes}`;
    }
  }
  updateKindleClock();
  setInterval(updateKindleClock, 10000);

  // ============================================================================
  // BAĞLANTIYI KOPYALA BUTONU
  // ============================================================================
  if (copyLinkBtn) {
    copyLinkBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      const urlToCopy = window.location.href;
      let ok = false;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        try {
          await navigator.clipboard.writeText(urlToCopy);
          ok = true;
        } catch (err) {}
      }
      if (!ok) {
        try {
          const tempInput = document.createElement('input');
          tempInput.value = urlToCopy;
          document.body.appendChild(tempInput);
          tempInput.select();
          document.execCommand('copy');
          document.body.removeChild(tempInput);
          ok = true;
        } catch (err) {}
      }
      if (copyTooltip) {
        copyTooltip.classList.remove('hidden');
        setTimeout(() => copyTooltip.classList.add('hidden'), 2000);
      }
    });
  }

  // ============================================================================
  // SIRADAN ÇIK / İPTAL ET BUTONU
  // ============================================================================
  if (cancelJobBtn) {
    cancelJobBtn.addEventListener('click', async () => {
      if (!currentJobId) {
        clearActiveJobFromStorage();
        cleanupActiveStreams();
        showSection(formSection);
        return;
      }
      try {
        await fetch(`${API_BASE_URL}/api/jobs/${currentJobId}/cancel`, { method: 'POST' });
      } catch (e) {
        console.warn('İptal isteği hatası:', e);
      }
      clearActiveJobFromStorage();
      cleanupActiveStreams();
      currentJobId = null;
      showSection(formSection);
    });
  }

  // ============================================================================
  // TAHMİNİ SÜRE (ETA) FORMATLAYICI VE GERÇEK ZAMANLI SAYAÇ
  // ============================================================================
  let etaCountdownTimer = null;
  let currentEtaSeconds = 0;
  let isQueueTimer = false;

  function formatEta(seconds) {
    if (!seconds || seconds <= 0) return null;
    if (seconds < 60) return `~${seconds} sn`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return secs > 0 ? `~${mins} dk ${secs} sn` : `~${mins} dk`;
  }

  function updateEtaDisplay(estimatedSeconds, status, isQueue = false) {
    if (!queueEtaBox || !queueEtaText) return;

    // İşlem bittiğinde/iptalde veya sırada 1. olup henüz aktarımı başlamamışken gizle
    if (status === 'completed' || status === 'failed' || status === 'cancelled' || (status === 'queued' && !isQueue)) {
      if (etaCountdownTimer) {
        clearInterval(etaCountdownTimer);
        etaCountdownTimer = null;
      }
      queueEtaBox.classList.add('hidden');
      return;
    }

    isQueueTimer = isQueue;
    if (estimatedSeconds && estimatedSeconds > 0) {
      currentEtaSeconds = estimatedSeconds;
    } else if (!currentEtaSeconds || currentEtaSeconds <= 0) {
      currentEtaSeconds = isQueue ? 25 : 12;
    }

    const label = isQueueTimer ? 'Tahmini Bekleme:' : 'Kalan Süre:';
    const formatted = formatEta(currentEtaSeconds);
    if (formatted) {
      queueEtaText.textContent = `${label} ${formatted}`;
      queueEtaBox.classList.remove('hidden');
    }

    if (!etaCountdownTimer) {
      etaCountdownTimer = setInterval(() => {
        if (currentEtaSeconds > 1) {
          currentEtaSeconds--;
          const f = formatEta(currentEtaSeconds);
          if (f && queueEtaText) {
            const prefix = isQueueTimer ? 'Tahmini Bekleme:' : 'Kalan Süre:';
            queueEtaText.textContent = `${prefix} ${f}`;
          }
        }
      }, 1000);
    }
  }

  // ============================================================================
  // YARDIMCI GÖRÜNÜM FONKSİYONLARI
  // ============================================================================
  function showSection(sectionToShow) {
    [formSection, progressSection, completedSection, errorSection].forEach(sec => {
      if (sec === sectionToShow) {
        sec.classList.remove('hidden');
      } else {
        sec.classList.add('hidden');
      }
    });
  }

  const inputStatusIcon = document.getElementById('inputStatusIcon');
  const usernameFeedback = document.getElementById('usernameFeedback');
  const inputDefaultHint = document.getElementById('inputDefaultHint');

  const STORAGE_KEY = 'active_1k_job';

  function extractUsername(rawInput) {
    let clean = (rawInput || '').trim();
    if (clean.startsWith('@')) {
      clean = clean.substring(1);
    }
    try {
      if (clean.includes('1000kitap.com/')) {
        const parts = clean.split('1000kitap.com/');
        clean = parts[1].split('/')[0].split('?')[0];
      }
    } catch (e) {}
    return clean.trim();
  }

  function validateUsername(rawInput) {
    const username = extractUsername(rawInput);
    if (!username) {
      return { valid: false, empty: true, message: '' };
    }
    if (username.length < 4) {
      return { valid: false, empty: false, message: 'Kullanıcı adı en az 4 karakter olmalıdır.' };
    }
    if (username.length > 30) {
      return { valid: false, empty: false, message: 'Kullanıcı adı en fazla 30 karakter olabilir.' };
    }
    if (!/^[a-zA-Z0-9_]+$/.test(username)) {
      return { valid: false, empty: false, message: 'Kullanıcı adınızda alt tire (_) dışında özel karakter ve Türkçe karakter olmamalıdır.' };
    }
    if (/^\d+$/.test(username)) {
      return { valid: false, empty: false, message: 'Kullanıcı adı sadece rakamlardan oluşamaz.' };
    }
    return { valid: true, empty: false, message: '', username };
  }

  function handleUsernameValidation() {
    const raw = usernameInput ? usernameInput.value : '';
    const res = validateUsername(raw);

    if (res.empty) {
      if (inputStatusIcon) {
        inputStatusIcon.className = 'input-status-icon hidden';
        inputStatusIcon.innerHTML = '';
      }
      if (usernameFeedback) {
        usernameFeedback.className = 'input-feedback hidden';
        usernameFeedback.textContent = '';
      }
      if (inputDefaultHint) inputDefaultHint.classList.remove('hidden');
      if (startBtn) startBtn.disabled = true;
      return false;
    }

    if (res.valid) {
      if (inputStatusIcon) {
        inputStatusIcon.className = 'input-status-icon is-valid';
        inputStatusIcon.innerHTML = '<i class="fa-solid fa-circle-check"></i>';
      }
      if (usernameFeedback) {
        usernameFeedback.className = 'input-feedback hidden';
        usernameFeedback.textContent = '';
      }
      if (inputDefaultHint) inputDefaultHint.classList.remove('hidden');
      if (startBtn) startBtn.disabled = false;
      return true;
    } else {
      if (inputStatusIcon) {
        inputStatusIcon.className = 'input-status-icon is-invalid';
        inputStatusIcon.innerHTML = '<i class="fa-solid fa-circle-xmark"></i>';
      }
      if (usernameFeedback) {
        usernameFeedback.className = 'input-feedback';
        usernameFeedback.innerHTML = `<i class="fa-solid fa-circle-exclamation"></i> <span>${res.message}</span>`;
      }
      if (inputDefaultHint) inputDefaultHint.classList.add('hidden');
      if (startBtn) startBtn.disabled = true;
      return false;
    }
  }

  if (usernameInput) {
    usernameInput.addEventListener('input', handleUsernameValidation);
    usernameInput.addEventListener('change', handleUsernameValidation);
    usernameInput.addEventListener('paste', () => setTimeout(handleUsernameValidation, 10));
  }
  handleUsernameValidation();

  function saveActiveJobToStorage(jobId, username, shelf, includeReviews) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        jobId,
        username,
        shelf,
        includeReviews,
        savedAt: Date.now()
      }));
    } catch (e) {}
  }

  function clearActiveJobFromStorage() {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (e) {}
  }

  function cleanupActiveStreams() {
    if (activeEventSource) {
      activeEventSource.close();
      activeEventSource = null;
    }
    if (activePollingInterval) {
      clearInterval(activePollingInterval);
      activePollingInterval = null;
    }
    if (etaCountdownTimer) {
      clearInterval(etaCountdownTimer);
      etaCountdownTimer = null;
    }
    currentEtaSeconds = 0;
    if (queueEtaBox) {
      queueEtaBox.classList.add('hidden');
    }
  }

  // State for silent auto-retry
  let currentUsername = '';
  let currentShelf = 'hepsi';
  let currentIncludeReviews = false;
  let autoRetryCount = 0;
  const MAX_AUTO_RETRIES = 3;

  async function startExportProcess(username, shelf, includeReviews = false) {
    currentUsername = username;
    currentShelf = shelf;
    currentIncludeReviews = includeReviews;

    // Buton durumunu ayarla
    startBtn.disabled = true;
    startBtn.innerHTML = '<span class="btn-text">KUYRUGA ALINIYOR...</span>';

    try {
      const response = await fetch(`${API_BASE_URL}/api/jobs`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ username, shelf, include_reviews: includeReviews })
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        const err = new Error(errData.detail || `Sunucu hatası (${response.status})`);
        err.status = response.status;
        throw err;
      }

      const data = await response.json();
      currentJobId = data.job_id;

      // İlerleme ekranını hazırla
      initProgressView(data);
      showSection(progressSection);

      // Canlı SSE akışı ve anlık durum sorgulamasını (polling) eşzamanlı başlat
      startEventStream(currentJobId);
      startFallbackPolling(currentJobId);

    } catch (err) {
      // 4xx istemci hatalarında (geçersiz kullanıcı adı, rate limit, mükerrer kuyruk vb.) otomatik retry yapma, kullanıcıya göster
      if (err.status && err.status >= 400 && err.status < 500) {
        showError(err.message);
        return;
      }

      if (autoRetryCount < MAX_AUTO_RETRIES) {
        autoRetryCount++;
        if (queueInfoText) queueInfoText.textContent = 'Bağlantı kuruluyor, lütfen bekleyin...';
        setTimeout(() => {
          startExportProcess(currentUsername, currentShelf, currentIncludeReviews);
        }, 1000);
      } else {
        showError(err.message || 'İş başlatılırken bir bağlantı hatası oluştu.');
      }
    } finally {
      startBtn.disabled = false;
      startBtn.innerHTML = '<span class="btn-text">KITAPLIGIMI AKTAR</span> <i class="fa-solid fa-arrow-right btn-arrow" aria-hidden="true"></i>';
    }
  }

  // ============================================================================
  // FORM GÖNDERME VE İŞ BAŞLATMA
  // ============================================================================
  exportForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const rawUsername = usernameInput.value;
    const res = validateUsername(rawUsername);

    if (!res.valid) {
      handleUsernameValidation();
      usernameInput.focus();
      return;
    }

    const username = res.username;
    const shelf = getSelectedShelf();
    const includeReviews = includeReviewsCheckbox ? includeReviewsCheckbox.checked : false;

    autoRetryCount = 0;
    startExportProcess(username, shelf, includeReviews);
  });

  // ============================================================================
  // İLERLEME EKRANI BAŞLATICI
  // ============================================================================
  function initProgressView(jobData) {
    const pos = jobData.queue_position !== undefined ? jobData.queue_position : (jobData.position !== undefined ? jobData.position : 1);
    const peopleAhead = jobData.people_ahead !== undefined ? jobData.people_ahead : (pos > 1 ? pos - 1 : 0);

    statusBadgeText.textContent = `SIRANIZ: #${pos}`;
    statusBadge.style.borderColor = 'var(--ink-espresso)';

    if (peopleAhead > 0) {
      queueInfoText.textContent = `Kuyruktasınız (Önünüzde ${peopleAhead} kişi var)...`;
      const waitSec = jobData.estimated_seconds || (peopleAhead * 22);
      updateEtaDisplay(waitSec, 'queued', true);
    } else {
      queueInfoText.textContent = `Sıranız: #${pos} (Aktarım başlatılıyor)...`;
      const waitSec = jobData.estimated_seconds || 3;
      updateEtaDisplay(waitSec, 'queued', true);
    }

    progressBar.style.width = '3%';
    progressBar.setAttribute('aria-valuenow', '3');
    countDisplay.textContent = '0 / --';
    percentDisplay.textContent = '%0';

    if (liveBookCard) liveBookCard.classList.add('hidden');
    if (liveBookCover) {
      liveBookCover.classList.add('hidden');
      liveBookCover.src = '';
    }
    if (bookPlaceholderIcon) bookPlaceholderIcon.classList.remove('hidden');
    if (liveBookTitle) liveBookTitle.textContent = '';
    if (liveBookAuthor) liveBookAuthor.textContent = '';
  }

  // ============================================================================
  // SERVER-SENT EVENTS (SSE) VE CANLI DURUM MOTORU
  // ============================================================================
  function startEventStream(jobId) {
    cleanupActiveStreams();

    const eventUrl = `${API_BASE_URL}/api/jobs/${jobId}/events`;
    activeEventSource = new EventSource(eventUrl);

    activeEventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleJobUpdate(data);
      } catch (err) {
        console.error('SSE veri ayrıştırma hatası:', err);
      }
    };

    activeEventSource.onerror = (err) => {
      console.warn('SSE bağlantısı kesildi, polling üzerinden devam ediliyor...', err);
      if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
      }
      startFallbackPolling(jobId);
    };
  }

  // SSE Gecikmelerine Karşı Kesintisiz Anlık JSON Polling Motoru
  function startFallbackPolling(jobId) {
    if (activePollingInterval) return;

    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/status`);
        if (!res.ok) {
          throw new Error('Görev durumu alınamadı.');
        }
        const data = await res.json();
        handleJobUpdate(data);

        if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
          cleanupActiveStreams();
        }
      } catch (e) {
        console.error('Polling hatası:', e);
      }
    };

    // İlk sorguyu 250ms sonra hemen yap, ardından 750ms aralıklarla sürdür
    setTimeout(poll, 250);
    activePollingInterval = setInterval(poll, 750);
  }

  // ============================================================================
  // GÖREV GÜNCELLEMELERİNİN EKRANA YANSITILMASI
  // ============================================================================
  function handleJobUpdate(data) {
    if (!data) return;

    // 0. İPTAL EDİLME DURUMU
    if (data.status === 'cancelled' || data.type === 'cancelled') {
      clearActiveJobFromStorage();
      cleanupActiveStreams();
      currentJobId = null;
      updateEtaDisplay(null);
      showSection(formSection);
      return;
    }

    // 1. KUYRUKTA BEKLEME DURUMU
    if (data.status === 'queued') {
      const pos = data.position !== undefined ? data.position : (data.queue_position !== undefined ? data.queue_position : 1);
      const peopleAhead = data.people_ahead !== undefined ? data.people_ahead : (pos > 1 ? pos - 1 : 0);

      statusBadgeText.textContent = `SIRANIZ: #${pos}`;
      statusBadge.style.borderColor = 'var(--ink-espresso)';

      if (peopleAhead > 0) {
        queueInfoText.textContent = `Kuyruktasınız (Önünüzde ${peopleAhead} kişi var)...`;
        const waitSec = data.estimated_seconds || (peopleAhead * 22);
        updateEtaDisplay(waitSec, 'queued', true);
      } else {
        queueInfoText.textContent = `Sıranız: #${pos} (Aktarım başlatılıyor)...`;
        const waitSec = data.estimated_seconds || 3;
        updateEtaDisplay(waitSec, 'queued', true);
      }
      return;
    }

    // 2. TARAMA, ISBN ÇÖZÜMLEME VEYA İNCELEME DURUMU
    const isReviews = data.status === 'fetching_reviews' || (data.message && (data.message.includes('inceleme') || data.message.includes('İnceleme')));
    const isIsbnResolving = data.status && (data.status.startsWith('resolving_isbn') || data.status === 'resolving_isbn') || (data.message && (data.message.includes('ISBN') || data.message.includes('Kitapyurdu')));
    const isScraping = data.status === 'scraping' || (data.status && data.status.startsWith('scraping_')) || data.type === 'progress' || data.type === 'status';

    if (isReviews || isIsbnResolving || isScraping) {
      if (isReviews) {
        statusBadgeText.textContent = 'İNCELEMELER ALINIYOR';
        statusBadge.style.borderColor = 'var(--accent-terracotta)';
      } else if (isIsbnResolving) {
        statusBadgeText.textContent = 'ISBN ÇÖZÜLÜYOR';
        statusBadge.style.borderColor = 'var(--accent-terracotta)';
      } else if (data.status === 'scraping_read' || data.shelf_display === 'Okuduklarım' || (data.message && data.message.includes('Okuduklarım'))) {
        statusBadgeText.textContent = 'OKUDUKLARIM TARANIYOR';
        statusBadge.style.borderColor = 'var(--accent-sage)';
      } else if (data.status === 'scraping_to_read' || data.shelf_display === 'Okumak İstediklerim' || (data.message && (data.message.includes('Okumak İstediklerim') || data.message.includes('Okunacak')))) {
        statusBadgeText.textContent = 'OKUNACAKLAR TARANIYOR';
        statusBadge.style.borderColor = 'var(--accent-sage)';
      } else if (data.status === 'scraping_currently_reading' || data.shelf_display === 'Şu An Okuduklarım' || (data.message && data.message.includes('Şu An Okuduklarım'))) {
        statusBadgeText.textContent = 'OKUNANLAR TARANIYOR';
        statusBadge.style.borderColor = 'var(--accent-sage)';
      } else {
        statusBadgeText.textContent = 'KİTAPLAR TARANIYOR';
        statusBadge.style.borderColor = 'var(--accent-sage)';
      }

      const current = data.current !== undefined ? data.current : (data.current_count !== undefined ? data.current_count : 0);
      const total = data.total !== undefined ? data.total : (data.total_count !== undefined ? data.total_count : 0);
      const percent = data.percent !== undefined ? data.percent : (total > 0 ? Math.min(99, Math.round((current / total) * 100)) : 0);

      if (isReviews) {
        queueInfoText.textContent = data.message || 'Kitap incelemeleri alınıyor ve eşleniyor...';
      } else if (isIsbnResolving) {
        queueInfoText.textContent = data.message || `ISBN numaraları tamamlanıyor: ${current} / ${total || '?'} (%${percent})...`;
      } else {
        queueInfoText.textContent = data.message || (total > 0 
          ? `Kitaplar taranıyor: ${current} / ${total} (%${percent})...`
          : `Kitaplar taranıyor: ${current} kitap bulundu...`);
      }

      countDisplay.textContent = total > 0 ? `${current} / ${total}` : `${current} / --`;
      percentDisplay.textContent = `%${percent}`;
      
      const barWidth = Math.max(3, Math.min(100, percent));
      progressBar.style.width = `${barWidth}%`;
      progressBar.setAttribute('aria-valuenow', String(percent));

      // Dinamik Kalan Süre (ETA): Backend gönderirse kullan, göndermezse kitap sayısından 9 kitap/sn ile hesapla
      let etaSec = data.estimated_seconds;
      if (!etaSec || etaSec <= 0) {
        if (total > 0 && current >= 0) {
          const rem = Math.max(0, total - current);
          etaSec = Math.ceil(rem / 9) + 1;
        } else {
          etaSec = 15;
        }
      }
      updateEtaDisplay(etaSec, data.status || 'scraping', false);

      // 1. Canlı Taranan Görsel Kitap Kartını Güncelle (Kapak + Başlık + Yazar)
      const lastBook = data.last_book;
      if (lastBook && lastBook.title) {
        if (liveBookTitle) liveBookTitle.textContent = lastBook.title;
        if (liveBookAuthor) liveBookAuthor.textContent = lastBook.author || 'Bilinmeyen Yazar';

        if (lastBook.cover && liveBookCover) {
          liveBookCover.src = lastBook.cover;
          liveBookCover.classList.remove('hidden');
          if (bookPlaceholderIcon) bookPlaceholderIcon.classList.add('hidden');
        } else if (liveBookCover) {
          liveBookCover.classList.add('hidden');
          if (bookPlaceholderIcon) bookPlaceholderIcon.classList.remove('hidden');
        }
        if (liveBookCard) liveBookCard.classList.remove('hidden');
      }
      return;
    }

    // 3. TAMAMLANMA DURUMU (COMPLETED)
    if (data.status === 'completed' || data.type === 'completed') {
      cleanupActiveStreams();
      updateEtaDisplay(null);

      // Yalnızca kullanıcı o sayfada aktif değilse (arka plandaysa) retro bildirim sesi çal ve sekme başlığını güncelle
      if (document.hidden) {
        playRetroCompletionChime();
        document.title = '✓ (TAMAMLANDI) 1000Kitap ➔ Goodreads';
      }

      const total = data.total || data.total_count || data.current || data.current_count || 0;
      autoRetryCount = 0;
      finalCount.textContent = total;
      if (data.preview_books && data.preview_books.length > 0) {
        previewBooks = data.preview_books;
      }

      let downloadUrl = data.download_url || `/api/jobs/${currentJobId}/download`;
      if (downloadUrl.startsWith('/')) {
        downloadUrl = `${API_BASE_URL}${downloadUrl}`;
      }
      downloadBtn.href = downloadUrl;

      showSection(completedSection);
      return;
    }

    // 4. HATA DURUMU (FAILED / ERROR)
    if (data.status === 'failed' || data.type === 'error') {
      cleanupActiveStreams();
      updateEtaDisplay(null);

      const errMsg = data.error || data.error_message || data.message || 'Bilinmeyen bir hata oluştu.';
      const isFatal = errMsg.includes('bulunamadı') || errMsg.includes('gizli') || errMsg.includes('Profil');

      // 403 veya geçici bağlantı hatalarında kullanıcıya hata göstermeden sessizce tekrar dene
      if (!isFatal && autoRetryCount < MAX_AUTO_RETRIES && currentUsername) {
        autoRetryCount++;
        statusBadgeText.textContent = 'HAZIRLANIYOR';
        statusBadge.style.borderColor = 'var(--ink-charcoal)';
        queueInfoText.textContent = 'Bağlantı kuruluyor, hazırlanıyor...';
        progressBar.style.width = '5%';
        progressBar.setAttribute('aria-valuenow', '5');

        setTimeout(() => {
          startExportProcess(currentUsername, currentShelf);
        }, 1200);
        return;
      }

      showError(errMsg);
    }
  }

  // ============================================================================
  // MANUEL DOSYA İNDİRME
  // ============================================================================
  downloadBtn.addEventListener('click', async (e) => {
    const url = downloadBtn.href;
    if (!url || url === '#' || url.endsWith('#')) return;

    e.preventDefault();
    try {
      const resp = await fetch(url);
      if (resp.ok) {
        const blob = await resp.blob();
        const blobUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = '1000kitap.csv';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
          document.body.removeChild(a);
          window.URL.revokeObjectURL(blobUrl);
        }, 1000);
        return;
      }
    } catch (err) {
      console.warn('Doğrudan indirmeye geçiliyor:', err);
    }
    window.location.href = url;
  });

  // ============================================================================
  // HATA VE SIFIRLAMA YÖNETİMİ
  // ============================================================================
  function showError(msg) {
    clearActiveJobFromStorage();
    cleanupActiveStreams();
    errorMessage.textContent = msg;

    if (privacyHelpBox) {
      const lower = (msg || '').toLowerCase();
      if (lower.includes('gizli') || lower.includes('boş') || lower.includes('bos') || lower.includes('bulunamadı') || lower.includes('bulunamadi') || lower.includes('profil') || lower.includes('ayarlar')) {
        privacyHelpBox.classList.remove('hidden');
      } else {
        privacyHelpBox.classList.add('hidden');
      }
    }

    showSection(errorSection);
  }

  resetBtn.addEventListener('click', () => {
    clearActiveJobFromStorage();
    cleanupActiveStreams();
    if (liveBookCard) liveBookCard.classList.add('hidden');
    if (liveBookCover) {
      liveBookCover.classList.add('hidden');
      liveBookCover.src = '';
    }
    if (bookPlaceholderIcon) bookPlaceholderIcon.classList.remove('hidden');
    if (privacyHelpBox) privacyHelpBox.classList.add('hidden');
    usernameInput.value = '';
    handleUsernameValidation();
    const defaultRadio = document.querySelector('input[name="shelf"][value="hepsi"]');
    if (defaultRadio) {
      defaultRadio.checked = true;
      updateRadioCardStyles();
    }
    showSection(formSection);
    usernameInput.focus();
  });

  retryBtn.addEventListener('click', () => {
    clearActiveJobFromStorage();
    cleanupActiveStreams();
    if (privacyHelpBox) privacyHelpBox.classList.add('hidden');
    handleUsernameValidation();
    showSection(formSection);
    usernameInput.focus();
  });

  // ============================================================================
  // RETRO BILDIRIM SESI VE SEKME YONETIMI
  // (Yalnizca kullanici o sayfada aktif degilken / baska sekmedeyken calar)
  // ============================================================================
  function playRetroCompletionChime() {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();

      // Rahatlatıcı, sıcak 8-bit retro arpej frekansları (E5, G5, B5, E6)
      const freqs = [659.25, 783.99, 987.77, 1318.51];
      const start = ctx.currentTime + 0.05;

      freqs.forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type = 'triangle'; // Yumuşak retro çip tınısı
        osc.frequency.setValueAtTime(freq, start + (i * 0.08));

        gain.gain.setValueAtTime(0, start + (i * 0.08));
        gain.gain.linearRampToValueAtTime(0.14, start + (i * 0.08) + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.001, start + (i * 0.08) + 0.20);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(start + (i * 0.08));
        osc.stop(start + (i * 0.08) + 0.21);
      });

      setTimeout(() => {
        try { ctx.close(); } catch (e) {}
      }, 900);
    } catch (e) {
      console.warn('Retro ses calinamadi:', e);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && document.title.includes('TAMAMLANDI')) {
      document.title = '1000Kitap ➔ Goodreads | Okuma Geçmişi Aktarıcı';
    }
  });

  // ============================================================================
  // ÖNİZLEME MODALI FONKSİYONLARI
  // ============================================================================
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function parseCsvLine(text) {
    const result = [];
    let cur = '';
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === '"') {
        if (inQuotes && text[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (c === ',' && !inQuotes) {
        result.push(cur.trim());
        cur = '';
      } else {
        cur += c;
      }
    }
    result.push(cur.trim());
    return result;
  }

  async function renderPreviewBooks() {
    if (!previewModalBody) return;

    if (!previewBooks || previewBooks.length === 0) {
      const downloadUrl = (downloadBtn && downloadBtn.href && !downloadBtn.href.endsWith('#')) 
        ? downloadBtn.href 
        : (currentJobId ? `${API_BASE_URL}/api/jobs/${currentJobId}/download` : null);

      if (downloadUrl) {
        previewModalBody.innerHTML = '<div style="text-align:center; padding:24px; color:var(--ink-medium);"><i class="fa-solid fa-spinner fa-spin" style="margin-right:8px;"></i> Önizleme hazırlanıyor...</div>';
        try {
          const resp = await fetch(downloadUrl);
          if (resp.ok) {
            const csvText = await resp.text();
            const lines = csvText.split(/\r?\n/).filter(l => l.trim().length > 0);
            if (lines.length > 1) {
              const headers = parseCsvLine(lines[0]);
              const titleIdx = headers.indexOf('Title');
              const authorIdx = headers.indexOf('Author');
              const isbnIdx = headers.indexOf('ISBN');
              const ratingIdx = headers.indexOf('My Rating');
              const shelfIdx = headers.indexOf('Exclusive Shelf');

              previewBooks = [];
              for (let i = 1; i < lines.length && previewBooks.length < 5; i++) {
                const cols = parseCsvLine(lines[i]);
                if (cols.length < 2) continue;
                const title = titleIdx !== -1 ? cols[titleIdx] : cols[0];
                const author = authorIdx !== -1 ? cols[authorIdx] : cols[1];
                let isbn = isbnIdx !== -1 ? cols[isbnIdx] : '';
                isbn = isbn.replace(/^[="]+|["]+$/g, '');
                const rating = ratingIdx !== -1 ? parseInt(cols[ratingIdx], 10) || 0 : 0;
                const rawShelf = shelfIdx !== -1 ? cols[shelfIdx] : 'read';
                const shelf = rawShelf === 'read' ? 'Okundu' : (rawShelf === 'to-read' ? 'Okunacak' : 'Şu An Okunuyor');

                previewBooks.push({
                  title: title || 'Bilinmeyen Kitap',
                  author: author || 'Bilinmeyen Yazar',
                  isbn: isbn,
                  rating: rating,
                  shelf: shelf
                });
              }
            }
          }
        } catch (err) {
          console.warn('Önizleme CSV yükleme hatası:', err);
        }
      }
    }

    if (!previewBooks || previewBooks.length === 0) {
      previewModalBody.innerHTML = '<p style="text-align:center; padding:20px; color:var(--ink-light); font-style:italic;">Önizleme verisi bulunamadı.</p>';
      return;
    }

    let html = '<div class="preview-book-list">';
    previewBooks.forEach((book, idx) => {
      const ratingStr = (book.rating && book.rating > 0) ? `${book.rating}/5 ★` : 'Puansız';
      html += `
        <div class="preview-book-item">
          <div class="preview-book-num">#${idx + 1}</div>
          <div class="preview-book-info">
            <div class="preview-book-title" title="${escapeHtml(book.title)}">${escapeHtml(book.title)}</div>
            <div class="preview-book-author">${escapeHtml(book.author || 'Bilinmeyen Yazar')}</div>
            <div class="preview-book-meta">
              <span class="preview-badge-shelf">${escapeHtml(book.shelf || 'Okundu')}</span>
              <span class="preview-badge-rating">${ratingStr}</span>
              ${book.isbn ? `<span style="color:#7D7063; font-family:monospace; font-size:0.70rem;">ISBN: ${escapeHtml(book.isbn)}</span>` : ''}
            </div>
          </div>
        </div>
      `;
    });
    html += '</div>';
    previewModalBody.innerHTML = html;
  }

  if (previewBtn) {
    previewBtn.addEventListener('click', async () => {
      if (previewModal) previewModal.classList.remove('hidden');
      await renderPreviewBooks();
    });
  }
  if (closePreviewBtn) {
    closePreviewBtn.addEventListener('click', () => {
      if (previewModal) previewModal.classList.add('hidden');
    });
  }
  if (closePreviewFooterBtn) {
    closePreviewFooterBtn.addEventListener('click', () => {
      if (previewModal) previewModal.classList.add('hidden');
    });
  }
  if (previewModal) {
    previewModal.addEventListener('click', (e) => {
      if (e.target === previewModal) {
        previewModal.classList.add('hidden');
      }
    });
  }

  // ============================================================================
  // SAYFA YENİLEME (F5) DAVRANIŞI: OTURUMU HER ZAMAN SIFIRDAN TEMİZ BAŞLAT
  // ============================================================================
  clearActiveJobFromStorage();

});
