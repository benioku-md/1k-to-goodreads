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

  // Canlı Kitap Önizleme Elemanları
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

  // Hata Elemanları
  const errorMessage = document.getElementById('errorMessage');
  const retryBtn = document.getElementById('retryBtn');

  // Durum Değişkenleri
  let activeEventSource = null;
  let activePollingInterval = null;
  let currentJobId = null;

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

  const USERNAME_REGEX = /^[a-zA-Z0-9_]{1,50}$/;
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

      // Tarayıcı hafızasına kaydet (F5 veya sayfa yenilemede oturumu korumak için)
      saveActiveJobToStorage(currentJobId, username, shelf, includeReviews);

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
    const username = extractUsername(rawUsername);
    const shelf = getSelectedShelf();
    const includeReviews = includeReviewsCheckbox ? includeReviewsCheckbox.checked : false;

    if (!username) {
      alert('Lütfen geçerli bir 1000Kitap kullanıcı adı girin.');
      usernameInput.focus();
      return;
    }

    if (!USERNAME_REGEX.test(username)) {
      alert('Kullanıcı adınızda alt tire (_) dışında özel karakter ve Türkçe karakter olmamalıdır.');
      usernameInput.focus();
      return;
    }

    autoRetryCount = 0;
    startExportProcess(username, shelf, includeReviews);
  });

  // ============================================================================
  // İLERLEME EKRANI BAŞLATICI
  // ============================================================================
  function initProgressView(jobData) {
    const pos = jobData.queue_position !== undefined ? jobData.queue_position : (jobData.position !== undefined ? jobData.position : 1);
    const waitCount = pos - 1;
    if (waitCount > 0) {
      statusBadgeText.textContent = `SIRANIZ: #${pos}`;
      statusBadge.style.borderColor = 'var(--ink-espresso)';
      queueInfoText.textContent = `Kuyruktasınız (Önünüzde ${waitCount} kişi var)...`;
    } else {
      statusBadgeText.textContent = 'BAŞLATILIYOR';
      statusBadge.style.borderColor = 'var(--ink-espresso)';
      queueInfoText.textContent = jobData.message || 'Sıradaki işlem sizin, aktarım başlatılıyor...';
    }

    progressBar.style.width = '3%';
    progressBar.setAttribute('aria-valuenow', '3');
    countDisplay.textContent = '0 / --';
    percentDisplay.textContent = '%0';

    liveBookCard.classList.add('hidden');
    liveBookCover.classList.add('hidden');
    liveBookCover.src = '';
    if (bookPlaceholderIcon) bookPlaceholderIcon.classList.remove('hidden');
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

        if (data.status === 'completed' || data.status === 'failed') {
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

    // 1. KUYRUKTA BEKLEME DURUMU
    if (data.status === 'queued') {
      const pos = data.position !== undefined ? data.position : (data.queue_position !== undefined ? data.queue_position : 1);
      const waitCount = pos - 1;
      if (waitCount > 0) {
        statusBadgeText.textContent = `SIRANIZ: #${pos}`;
        statusBadge.style.borderColor = 'var(--ink-espresso)';
        queueInfoText.textContent = `Kuyruktasınız (Önünüzde ${waitCount} kişi var)...`;
        return;
      }
      statusBadgeText.textContent = 'BAŞLATILIYOR';
      statusBadge.style.borderColor = 'var(--ink-espresso)';
      queueInfoText.textContent = data.message || 'Sıradaki işlem sizin, aktarım başlatılıyor...';
      return;
    }

    // 2. TARAMA VEYA ISBN ÇÖZÜMLEME DURUMU
    const isIsbnResolving = data.status && (data.status.startsWith('resolving_isbn') || data.status === 'resolving_isbn');
    if (data.status === 'scraping' || isIsbnResolving || data.type === 'progress' || data.type === 'status') {
      if (data.status === 'resolving_isbn_strict') {
        statusBadgeText.textContent = 'ISBN (KATI MOD)';
        statusBadge.style.borderColor = 'var(--accent-terracotta)';
      } else if (data.status === 'resolving_isbn_loose') {
        statusBadgeText.textContent = 'ISBN (GEVŞEK MOD)';
        statusBadge.style.borderColor = 'var(--accent-terracotta)';
      } else if (data.status === 'resolving_isbn_ky' || isIsbnResolving) {
        statusBadgeText.textContent = 'ISBN ÇÖZÜLÜYOR';
        statusBadge.style.borderColor = 'var(--accent-terracotta)';
      } else {
        statusBadgeText.textContent = 'TARANIYOR';
        statusBadge.style.borderColor = 'var(--accent-sage)';
      }

      const current = data.current !== undefined ? data.current : (data.current_count !== undefined ? data.current_count : 0);
      const total = data.total !== undefined ? data.total : (data.total_count !== undefined ? data.total_count : 0);
      const percent = data.percent !== undefined ? data.percent : (total > 0 ? Math.min(99, Math.round((current / total) * 100)) : 0);

      if (isIsbnResolving || (data.message && (data.message.includes('ISBN') || data.message.includes('Kitapyurdu')))) {
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

      // Canlı taranan kitap kartını güncelle
      const lastBook = data.last_book;
      if (lastBook && lastBook.title) {
        liveBookTitle.textContent = lastBook.title;
        liveBookAuthor.textContent = lastBook.author || 'Bilinmeyen Yazar';

        if (lastBook.cover) {
          liveBookCover.src = lastBook.cover;
          liveBookCover.classList.remove('hidden');
          if (bookPlaceholderIcon) bookPlaceholderIcon.classList.add('hidden');
        } else {
          liveBookCover.classList.add('hidden');
          if (bookPlaceholderIcon) bookPlaceholderIcon.classList.remove('hidden');
        }
        liveBookCard.classList.remove('hidden');
      }
      return;
    }

    // 3. TAMAMLANMA DURUMU (COMPLETED)
    if (data.status === 'completed' || data.type === 'completed') {
      cleanupActiveStreams();

      const total = data.total || data.total_count || data.current || data.current_count || 0;
      autoRetryCount = 0;
      finalCount.textContent = total;

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
    showSection(errorSection);
  }

  resetBtn.addEventListener('click', () => {
    clearActiveJobFromStorage();
    cleanupActiveStreams();
    usernameInput.value = '';
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
    showSection(formSection);
    usernameInput.focus();
  });

  // ============================================================================
  // SAYFA YENİLEME VE DEVAM EDEN GÖREVİ GERİ YÜKLEME (LocalStorage)
  // ============================================================================
  async function checkSavedActiveJob() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (!saved) return;
      const parsed = JSON.parse(saved);
      if (!parsed || !parsed.jobId) {
        clearActiveJobFromStorage();
        return;
      }

      // 15 dakikadan eskiyse süresi dolmuştur, temizle
      if (Date.now() - (parsed.savedAt || 0) > 15 * 60 * 1000) {
        clearActiveJobFromStorage();
        return;
      }

      const res = await fetch(`${API_BASE_URL}/api/jobs/${parsed.jobId}/status`);
      if (!res.ok) {
        clearActiveJobFromStorage();
        return;
      }

      const data = await res.json();
      currentJobId = parsed.jobId;
      currentUsername = parsed.username || '';
      currentShelf = parsed.shelf || 'hepsi';
      currentIncludeReviews = !!parsed.includeReviews;

      if (data.status === 'completed') {
        showSection(completedSection);
        handleJobUpdate(data);
      } else if (data.status === 'failed') {
        clearActiveJobFromStorage();
      } else {
        initProgressView(data);
        showSection(progressSection);
        handleJobUpdate(data);
        startEventStream(currentJobId);
        startFallbackPolling(currentJobId);
      }
    } catch (e) {
      console.warn('Aktif görev geri yükleme hatası:', e);
    }
  }

  // Sayfa açıldığında hafızada bekleyen veya devam eden bir görev varsa geri bağla
  checkSavedActiveJob();

});
