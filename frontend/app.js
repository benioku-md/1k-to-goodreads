/**
 * 1000Kitap to Goodreads Exporter - Frontend Kontrolcüsü
 * Vanilla JavaScript (ES6+), Sıfır Bağımlılık, SSE ve Otomatik Fallback Desteği
 */

document.addEventListener('DOMContentLoaded', () => {
  // Canlı Backend API URL'i (GitHub Pages üzerindeyken Render'a bağlanır, yereldeyken göreceli çalışır)
  const API_BASE_URL = window.location.hostname.includes('github.io')
    ? 'https://onek-to-goodreads.onrender.com'
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
  const shelfInput = document.getElementById('shelfInput');
  const startBtn = document.getElementById('startBtn');

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

  function cleanUsernameInput(rawInput) {
    let clean = rawInput.trim();
    // @ sembolünü kaldır
    if (clean.startsWith('@')) {
      clean = clean.substring(1);
    }
    // URL girildiyse (örn: https://1000kitap.com/kullanici_adi) kullanıcı adını ayıkla
    try {
      if (clean.includes('1000kitap.com/')) {
        const parts = clean.split('1000kitap.com/');
        clean = parts[1].split('/')[0].split('?')[0];
      }
    } catch (e) {
      // Hata olursa ham halini kullan
    }
    return clean.replace(/[^a-zA-Z0-9_\-\.]/g, '');
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

  // ============================================================================
  // FORM GÖNDERME VE İŞ BAŞLATMA
  // ============================================================================
  exportForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const rawUsername = usernameInput.value;
    const username = cleanUsernameInput(rawUsername);
    const shelf = shelfInput.value || 'okuduklari';

    if (!username) {
      alert('Lütfen geçerli bir 1000Kitap kullanıcı adı girin.');
      usernameInput.focus();
      return;
    }

    // Buton durumunu ayarla
    startBtn.disabled = true;
    startBtn.innerHTML = '<span class="btn-text">KUYRUGA ALINIYOR...</span>';

    try {
      const response = await fetch(`${API_BASE_URL}/api/jobs`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ username, shelf })
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Sunucu hatası (${response.status})`);
      }

      const data = await response.json();
      currentJobId = data.job_id;

      // İlerleme ekranını hazırla
      initProgressView(data);
      showSection(progressSection);

      // Canlı SSE akışını başlat
      startEventStream(currentJobId);

    } catch (err) {
      showError(err.message || 'İş başlatılırken bir bağlantı hatası oluştu.');
    } finally {
      startBtn.disabled = false;
      startBtn.innerHTML = '<span class="btn-text">KITAPLIGIMI AKTAR</span> <i class="fa-solid fa-arrow-right btn-arrow" aria-hidden="true"></i>';
    }
  });

  // ============================================================================
  // İLERLEME EKRANI BAŞLATICI
  // ============================================================================
  function initProgressView(jobData) {
    statusBadgeText.textContent = 'KUYRUGA ALINDI';
    statusBadge.style.borderColor = 'var(--ink-espresso)';
    queueInfoText.textContent = jobData.queue_position > 1 
      ? 'Sıradasınız, önceki işlem tamamlanınca aktarımınız başlayacak...' 
      : 'İşleminiz hazırlanıyor, aktarım başlıyor...';

    progressBar.style.width = '3%';
    progressBar.setAttribute('aria-valuenow', '3');
    countDisplay.textContent = '0 / --';
    percentDisplay.textContent = '%0';

    liveBookCard.classList.add('hidden');
    liveBookCover.classList.add('hidden');
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
      console.warn('SSE bağlantısı kesildi, fallback polling devreye giriyor...', err);
      if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
      }
      // SSE başarısız olursa JSON polling başlat
      startFallbackPolling(jobId);
    };
  }

  // SSE Kesintisi Durumunda Otomatik JSON Polling
  function startFallbackPolling(jobId) {
    if (activePollingInterval) return;

    activePollingInterval = setInterval(async () => {
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
    }, 2000);
  }

  // ============================================================================
  // GÖREV GÜNCELLEMELERİNİN EKRANA YANSITILMASI
  // ============================================================================
  function handleJobUpdate(data) {
    // 1. KUYRUKTA BEKLEME DURUMU
    if (data.status === 'queued') {
      statusBadgeText.textContent = 'SIRADA';
      const pos = data.position || data.queue_position || 1;
      queueInfoText.textContent = pos > 1 
        ? 'Sıradasınız, önceki işlem tamamlanınca aktarımınız başlayacak...' 
        : 'İşleminiz hazırlanıyor, aktarım başlıyor...';
      return;
    }

    // 2. TARAMA DURUMU (SCRAPING / PROGRESS)
    if (data.status === 'scraping' || data.type === 'progress') {
      statusBadgeText.textContent = 'TARANIYOR';
      statusBadge.style.borderColor = 'var(--accent-sage)';

      const current = data.current || data.current_count || 0;
      const total = data.total || data.total_count || 0;
      const percent = data.percent !== undefined ? data.percent : Math.min(99, Math.round((current / (total || 1)) * 100));

      queueInfoText.textContent = total > 0 
        ? `Kitaplar taranıyor: ${current} / ${total} (%${percent})...`
        : `Kitaplar taranıyor: ${current} kitap bulundu...`;

      countDisplay.textContent = total > 0 ? `${current} / ${total}` : `${current} / ?`;
      percentDisplay.textContent = `%${percent}`;
      
      const barWidth = Math.max(5, Math.min(100, percent));
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
        } else {
          liveBookCover.classList.add('hidden');
        }
        liveBookCard.classList.remove('hidden');
      }
      return;
    }

    // 3. TAMAMLANMA DURUMU (COMPLETED)
    if (data.status === 'completed' || data.type === 'completed') {
      cleanupActiveStreams();

      const total = data.total || data.total_count || data.current || data.current_count || 0;
      finalCount.textContent = total;

      let downloadUrl = data.download_url || `/api/jobs/${currentJobId}/download`;
      if (downloadUrl.startsWith('/')) {
        downloadUrl = `${API_BASE_URL}${downloadUrl}`;
      }
      downloadBtn.href = downloadUrl;

      showSection(completedSection);

      // CSV'yi otomatik indirme tetiklemesi
      triggerAutoDownload(downloadUrl);
      return;
    }

    // 4. HATA DURUMU (FAILED / ERROR)
    if (data.status === 'failed' || data.type === 'error') {
      cleanupActiveStreams();
      showError(data.error || data.error_message || data.message || 'Bilinmeyen bir hata oluştu.');
    }
  }

  // ============================================================================
  // OTOMATİK DOSYA İNDİRME
  // ============================================================================
  function triggerAutoDownload(url) {
    try {
      const a = document.createElement('a');
      a.href = url;
      a.download = '1000kitap.csv';
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        document.body.removeChild(a);
      }, 1000);
    } catch (e) {
      console.warn('Otomatik indirme tetiklenemedi:', e);
    }
  }

  // ============================================================================
  // HATA VE SIFIRLAMA YÖNETİMİ
  // ============================================================================
  function showError(msg) {
    cleanupActiveStreams();
    errorMessage.textContent = msg;
    showSection(errorSection);
  }

  resetBtn.addEventListener('click', () => {
    cleanupActiveStreams();
    usernameInput.value = '';
    showSection(formSection);
    usernameInput.focus();
  });

  retryBtn.addEventListener('click', () => {
    cleanupActiveStreams();
    showSection(formSection);
    usernameInput.focus();
  });

});
