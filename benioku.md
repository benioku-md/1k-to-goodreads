# 📚 1000Kitap'tan Goodreads'e Okuma Geçmişi Aktarıcı (1k-to-goodreads)

**1k-to-goodreads**; 1000Kitap üzerindeki okuma geçmişinizi, okuma tarihlerinizi, puanlarınızı ve kitap kapaklarınızı toplayıp akıllı **3 Kademeli ISBN Çözümleme Motoru** ile zenginleştirerek resmî **Goodreads CSV formatına** dönüştüren; Kindle estetiğinde tasarlanmış, veri tabanı barındırmayan, kullanıcı kaydı ve log tutmayan açık kaynaklı bir araçtır.

Canlı Sürüm: [benioku-md.github.io/1k-to-goodreads](https://benioku-md.github.io/1k-to-goodreads/)

---

## 🎯 Temel Özellikler
- **3 Kademeli Akıllı ISBN Motoru:** Kitap adlarının Goodreads'te tam uyuşması için Google Books ve Kitapyurdu üzerinden kademeli ISBN taraması.
- **Goodreads ile %100 Uyum:** Goodreads CSV içe aktarıcıs için özel olarak optimize edilmiş standart 6 sütunlu CSV çıktısı.
- **Canlı SSE Akışı:** İşlem sırası, taranan kitap sayısı, anlık yüzde ve okunan son kitabın kapak önizlemesi canlı olarak gösterilir.
- **Sıfır Günlükleme & Yüksek Mahremiyet:** Kullanıcı parolası istenmez, hiçbir veri diske yazılmaz, işlem bitiminde RAM tamamen temizlenir.

---

## 🔍 Akıllı 3 Kademeli ISBN Çözümleme Mimarisi
Goodreads'e yapılan aktarımlarda kitap adı ve yazar uyuşmazlıklarını sıfıra indirmek amacıyla sistem kademeli bir ISBN araması çalıştırır:

1. **Google Books Katı Mod (`intitle` + `inauthor`):**
   Tüm liste için aynı anda 5 paralel asenkron sorgu ile Google Books üzerinden resmî arama yapılır. Tüm kitaplar bulunursa sonraki aşamalar devreye girmez.
2. **Google Books Gevşek Mod (`q=Kitap Yazar`):**
   Yayınevi veya çevirmen farklılığından dolayı ilk aşamada bulunamayan kitaplar için serbest metin araması yapılır.
3. **Kitapyurdu (15x Paralel Turbo Motoru):**
   Google üzerinde bulunamayan nadir veya yerli baskılar için Kitapyurdu kataloğu taranarak ISBN cımbızlanır.

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
- **Canlı Akış & Otomatik Yeniden Bağlanma:** Server-Sent Events (SSE) ile kesintisiz durum aktarımı sağlanır; ağ kopmalarında otomatik JSON Polling desteği devreye girer.
- **CSV Injection Koruması:** E-tablo programlarının (Excel, Google Sheets vb.) formül çalıştırmasını engellemek amacıyla `=`, `+`, `-`, `@` ile başlayan riskli metinler `sanitize_csv_field` ile filtrelenir.
- **Temiz Karakter Kodlaması:** Karakter kaymalarını ve Goodreads içe aktarıcısının ilk sütunu boşa düşürmesini engellemek için dosya standart UTF-8 olarak sunulur.

---

## 📌 Diğer Meseleler

- **Alternatif Platformlar:** Bu araç yalnızca Goodreads ile sınırlı değildir; StoryGraph veya CSV içe aktarımını destekleyen herhangi bir kitap takip platformunda da rahatlıkla kullanılabilir. Yalnızca gerekli sütunları tablo üzerinden değiştirmeniz yeterlidir.
- **Gizli Raflar:** Okuma geçmişinin çekilebilmesi için ilgili 1000Kitap profilindeki "Okuduklarım" rafının gizli olmaması gerekmektedir. Şayet rafınız gizliyse, aktarım yapmadan önce 1000Kitap Gizlilik ayarlarından geçici olarak herkese açık hâle getirmeniz gerekir.
- **Ekstra Mahremiyet Tavsiyesi:** Bu araç sadece kullanıcı adınıza ihtiyaç duyar ve bunu hiçbir yerde depolamaz. Yine de en üst düzey mahremiyet isteyen kullanıcıların, aracı çalıştırmadan önce 1000Kitap üzerinden kullanıcı adlarını geçici olarak değiştirip ve kişisel bilgilerini de gizleyerek aktarım sonrası eski adlarına dönmeleri tavsiye edilir.
