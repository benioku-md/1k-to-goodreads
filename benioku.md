# 📚 1000Kitap'tan Goodreads'e Okuma Geçmişi Aktarıcı (1k-to-goodreads)

**1k-to-goodreads**; 1000Kitap üzerindeki okuma geçmişinizi, okuma tarihlerinizi, puanlarınızı, tekrar okuma sayılarınızı ve isteğe bağlı olarak kitap incelemelerinizi toplayıp akıllı **Kitapyurdu ISBN Çözümleme Motoru** ile zenginleştirerek resmî **Goodreads CSV formatına** dönüştüren; Kindle estetiğinde tasarlanmış, veri tabanı barındırmayan, kullanıcı kaydı ve erişim günlüğü tutmayan açık kaynaklı bir araçtır.

Canlı Sürüm: <a href="https://benioku-md.github.io/1k-to-goodreads/" target="_blank" rel="noopener noreferrer">benioku-md.github.io/1k-to-goodreads</a>

---

## 🎯 Temel Özellikler

- **Goodreads ile %100 Resmî CSV Uyumu:** Goodreads CSV içe aktarıcısının beklediği sütunlar (`Title`, `Author`, `ISBN`, `My Rating`, `Date Read`, `Bookshelves`, `Exclusive Shelf`, `Read Count`, `My Review`) ile tam entegrasyon.
- **Sınırsız ve Akıllı ISBN Motoru:** Harici API kotalarına veya günlük sınırlarına takılmayan, eşzamanlı Kitapyurdu ISBN tarama ve başlık eşleme motoru.
- **Tüm Raflar ve Otomatik "Şu An Okuduklarım":**
  - **Tüm Kütüphane:** Okuduklarım, Şu An Okuduklarım ve Okumak İstediklerim raflarının tamamı.
  - **Okuduklarım:** Okunan kitapların yanında o an okunmakta olan kitaplar (`currently-reading`) arka planda otomatik olarak dâhil edilir.
  - **Okumak İstediklerim:** Gelecekte okunacak kitaplar (`to-read`).
- **Kitap İncelemeleri (My Review) Desteği:**
  - İsteğe bağlı bağımsız onay kutusu ile kullanıcının 1000Kitap'ta yazdığı incelemeler çekilir.
- **Tekrar Okuma Sayısı (Read Count):** 1000Kitap'ta birden fazla kez okunduğu belirtilen kitaplar Goodreads'in `Read Count` sütununa otomatik işlenir.
- **Canlı İlerleme ve Kapak Önizlemesi:** İşlem sırası, taranan kitap sayısı, anlık yüzde ve son işlenen kitabın kapak önizlemesi arayüzde canlı akar.

---

## 🔍 Akıllı ISBN Çözümleme

Goodreads'e yapılan aktarımlarda kitap adı ve yazar uyuşmazlıklarını minimize etmek amacıyla Kitapyurdu üzerinden çok katmanlı bir ISBN taraması çalıştırılır:

1. **Akıllı Başlık Puanlama:** Arama sonuçlarındaki tüm kitaplar; kitap adı, anahtar kelimeler ve bağlantı üzerinden puanlanır; doğru kitap tam isabetle seçilir.
2. **Çok Aşamalı Temizleme:** Alt başlıklar, cilt numaraları ve parantez içi yayınevi ekleri temizlenerek ardışık sorgularla özel baskı kitaplar yakalanır.
3. **Kapsamlı ISBN Tespiti:** JSON-LD Schema, Ürün Özellikleri ve Sayfa İçi taramayla nadir ve eski baskılar dahil tüm ISBN numaraları eksiksiz ayıklanır.

---

## 🛡️ Mahremiyet, Veri Güvenliği ve Şeffaflık

Bu araç, kullanıcı mahremiyetini en temel ilke olarak kabul eder. Sistemin teknik işleyişi şeffaf bir şekilde aşağıda açıklanmıştır:

1. **Kalıcı Veri Tabanı Yok (Zero-Disk):** Sistemde hiçbir SQL veya NoSQL (PostgreSQL, SQLite, Redis vb.) veri tabanı bulunmaz. Verileriniz kalıcı bir diske kaydedilmez.
2. **Erişim Günlüğü Yok (Zero-Log):** Web sunucusunun erişim logları (`access logs`) kapalıdır. IP adresiniz veya kullanıcı adınız sunucu log dosyalarına yazılmaz.
3. **Parolasız İşlem:** Hesap parolanız asla talep edilmez. Yalnızca 1000Kitap kullanıcı adınız üzerinden herkese açık veriler taranır.
4. **Geçici Bellek (RAM) ve Otomatik İmha:** Kitaplarınız ve varsa incelemeleriniz, yalnızca aktarım ve ISBN arama sürecinde sunucunun geçici belleğinde (RAM) tutulur. CSV dosyası üretildikten sonra 15 dakika içinde RAM'den tamamen silinir.
5. **Açık Kaynak Güvencesi:** Tüm kaynak kodlar açıktır. Dileyen herkes kodları satır satır inceleyebilir, bağımsız olarak kendi bilgisayarında veya kendi sunucusunda çalıştırabilir.

---

## 📌 Önemli Notlar ve İpuçları

- **Kütüphanenizin Herkese Açık Olması:** 1000Kitap profiliniz veya raflarınız gizliyse (yalnızca kendinize veya takipçilerinize açıksa), sistem kitaplarınızı çekemez. Aktarım yapmadan önce 1000Kitap **Profil Ayarları ➔ Gizlilik** bölümünden raflarınızı geçici olarak **herkese açık** hâle getirmeniz gerekir. Aktarım tamamlanıp CSV dosyanızı indirdikten sonra gizlilik ayarlarınızı dilediğiniz an tekrar eski hâline getirebilirsiniz.
- **Alternatif Platformlar:** Üretilen CSV dosyası yalnızca Goodreads ile sınırlı değildir. StoryGraph veya standart CSV içe aktarımını destekleyen diğer tüm kitap takip servislerinde de doğrudan kullanılabilir (kullanılabilmesi için tablo sütunlarının isimlerini değiştirmeniz gerekebilir!!!).
- **Maksimum Mahremiyet Tavsiyesi:** Sistem kullanıcı adınızı depolamaz fakat 1000Kitap kullanıcı adınızın aktarım sunucusuna gitmesini dahi istemiyorsanız, işlem öncesinde 1000Kitap profilinizden kullanıcı adınızı geçici bir adla değiştirip aktarımı tamamladıktan sonra eski kullanıcı adınıza geri dönebilirsiniz.
