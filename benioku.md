# 📚 1000Kitap'tan Goodreads'e Okuma Geçmişi Aktarıcı (1k-to-goodreads)

**1k-to-goodreads**; 1000Kitap üzerindeki okuma geçmişinizi, okuma tarihlerinizi, puanlarınızı ve kitap kapaklarınızı toplayıp akıllı **Kitapyurdu ISBN Çözümleme Motoru** ile zenginleştirerek resmî **Goodreads CSV formatına** dönüştüren; Kindle estetiğinde tasarlanmış, veri tabanı barındırmayan, kullanıcı kaydı ve log tutmayan açık kaynaklı bir araçtır.

Canlı Sürüm: <a href="http://5.175.136.60:8085" target="_blank" rel="noopener noreferrer">http://5.175.136.60:8085</a> | <a href="https://benioku-md.github.io/1k-to-goodreads/" target="_blank" rel="noopener noreferrer">benioku-md.github.io/1k-to-goodreads</a>

---

## 🎯 Temel Özellikler
- **Sınırsız ve Akıllı ISBN Motoru:** Harici API kotalarına ve günlük istek sınırlarına takılmayan, 5 paralel asenkron iş parçacıklı Kitapyurdu ISBN tarama motoru.
- **Goodreads ile %100 Uyum:** Goodreads CSV içe aktarıcısı için özel olarak optimize edilmiş standart 6 sütunlu CSV çıktısı.
- **Canlı SSE Akışı:** İşlem sırası, taranan kitap sayısı, anlık yüzde ve okunan son kitabın kapak önizlemesi canlı olarak gösterilir.
- **Sıfır Günlükleme ve Yüksek Mahremiyet:** Kullanıcı parolası istenmez, hiçbir veri diske yazılmaz, işlem bitiminde RAM tamamen temizlenir.

---

## 🔍 Akıllı ISBN Çözümleme Mimarisi
Goodreads'e yapılan aktarımlarda kitap adı ve yazar uyuşmazlıklarını sıfıra indirmek ve doğru baskıları yakalamak amacıyla sistem çok katmanlı bir ISBN taraması çalıştırır:

1. **Akıllı Başlık Puanlama Algoritması:**
   Arama sonuçlarındaki tüm kitaplar; kitap adı, anahtar kelimeler ve sayfa bağlantısı üzerinden puanlanır. Popüler reklamlar veya aynı yazarın farklı kitapları elenerek aranan kitap tam isabetle seçilir.
2. **Çok Aşamalı Başlık Temizleme:**
   Alt başlıklar, cilt numaraları ve parantez içi yayınevi ekleri temizlenerek ardışık sorgularla nadir veya özel baskı kitaplar yakalanır.
3. **3 Kademeli ISBN Ayıklama:**
   JSON-LD Schema, Ürün Özellikleri Tablosu ve Sayfa İçi Regex taramasıyla tükenmiş, nadir ve 2007 öncesi 10 haneli (975...) baskılar dahil tüm ISBN numaraları eksiksiz ayıklanır.
4. **Cloudflare Uyumlu 5x Asenkron Paralel Motor:**
   5 eşzamanlı asenkron iş parçacığı ve akıllı mikro bekleme aralıklarıyla saniyede ortalama 3-4 kitap taranır; Cloudflare engellerine takılmadan yüksek hızda sonuç üretilir.

---

## 🛡️ Mahremiyet ve Sıfır Log
1. **Veri Tabanı Yok:** Sistemde hiçbir SQL, SQLite, Redis veya NoSQL veri tabanı bulunmaz.
2. **Erişim Günlüğü Yok:** Uvicorn ve API sunucusunda IP adresleri ve kullanıcı adları kaydedilmez, loglar devre dışıdır.
3. **Parolasız ve Güvenli:** Yalnızca herkese açık kütüphane taranır; hesap şifresi veya oturum bilgisi kesinlikle talep edilmez.
4. **Anında Bellek Temizliği:** Üretilen CSV dosyası kullanıcı tarafından indirildiği anda (veya indirmese bile 15 dakika sonra) RAM'den silinir ve bellek tamamen serbest bırakılır.

---

## ⚙️ Teknik Mimari
- **Ön Yüz:** Saf HTML5, CSS3 ve Modern Vanilla JavaScript (ES6+). Sıfır CDN ve harici kütüphane bağımlılığı.
- **Arka Yüz (Backend):** Python 3.10+, FastAPI ve Uvicorn.
- **TLS / WAF Koruma Katmanı:** 1000Kitap mobil API'si ile haberleşirken Cloudflare WAF engellerini aşmak ve güvenliği sağlamak için `curl_cffi` ile Chrome/Android parmak izi kimliklendirmesi kullanılır.
- **Hız Sınırlamalı FIFO Kuyruk:** Sunucu kaynaklarını korumak ve 1000Kitap servislerini yormamak adına `asyncio.Queue` ile kullanıcılar sıraya alınır; 900ms güvenlik gecikmesiyle taranır.
- **Canlı Akış ve Otomatik Yeniden Bağlanma:** Server-Sent Events (SSE) ile kesintisiz durum aktarımı sağlanır; ağ kopmalarında otomatik JSON Polling desteği devreye girer.
- **CSV Injection Koruması:** E-tablo programlarının (Excel, Google Sheets vb.) formül çalıştırmasını engellemek amacıyla `=`, `+`, `-`, `@` ile başlayan riskli metinler `sanitize_csv_field` ile filtrelenir.
- **Temiz Karakter Kodlaması:** Karakter kaymalarını ve Goodreads içe aktarıcısının ilk sütunu boşa düşürmesini engellemek için dosya standart UTF-8 olarak sunulur.

---

## 📌 Diğer Meseleler

- **Alternatif Platformlar:** Bu araç yalnızca Goodreads ile sınırlı değildir; StoryGraph veya CSV içe aktarımını destekleyen herhangi bir kitap takip platformunda da rahatlıkla kullanılabilir. Yalnızca gerekli sütunları tablo üzerinden değiştirmeniz yeterlidir.
- **Gizli Raflar:** Okuma geçmişinin çekilebilmesi için ilgili 1000Kitap profilindeki "Okuduklarım" rafının gizli olmaması gerekmektedir. Şayet rafınız gizliyse, aktarım yapmadan önce 1000Kitap Gizlilik ayarlarından geçici olarak herkese açık hâle getirmeniz gerekir.
- **Ekstra Mahremiyet Tavsiyesi:** Bu araç sadece kullanıcı adınıza ihtiyaç duyar ve bunu hiçbir yerde depolamaz. Yine de en üst düzey mahremiyet isteyen kullanıcıların, aracı çalıştırmadan önce 1000Kitap üzerinden kullanıcı adlarını geçici olarak değiştirip ve kişisel bilgilerini de gizleyerek aktarım sonrası eski adlarına dönmeleri tavsiye edilir.
