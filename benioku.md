# 📚 1000Kitap'tan Goodreads'e Okuma Geçmişi Aktarıcı (1k-to-goodreads)

**1k-to-goodreads**; 1000Kitap üzerindeki okuma geçmişinizi, okuma tarihlerinizi, puanlarınızı ve kitap kapaklarınızı toplayıp akıllı **Kitapyurdu ISBN Çözümleme Motoru** ile zenginleştirerek resmî **Goodreads CSV formatına** dönüştüren; Kindle estetiğinde tasarlanmış, veri tabanı barındırmayan, kullanıcı kaydı ve log tutmayan açık kaynaklı bir araçtır.

Canlı Sürüm: <a href="https://benioku-md.github.io/1k-to-goodreads/" target="_blank" rel="noopener noreferrer">benioku-md.github.io/1k-to-goodreads</a>

---

## 🎯 Temel Özellikler
- **Sınırsız ve Akıllı ISBN Motoru:** Harici API kotalarına ve günlük sınırlarına takılmayan, eşzamanlı Kitapyurdu ISBN tarama motoru.
- **Goodreads ile %100 Uyum:** Goodreads CSV içe aktarıcısı için özel olarak optimize edilmiş standart 6 sütunlu CSV çıktısı.
- **Canlı Durum Akışı:** İşlem sırası, taranan kitap sayısı, anlık yüzde ve okunan son kitabın kapak önizlemesi canlı olarak gösterilir.
- **Sıfır Günlükleme ve Yüksek Mahremiyet:** Kullanıcı parolası istenmez, hiçbir veri diske yazılmaz, işlem bitiminde bellek tamamen temizlenir.

---

## 🔍 Akıllı ISBN Çözümleme
Goodreads'e yapılan aktarımlarda kitap adı ve yazar uyuşmazlıklarını sıfıra indirmek amacıyla sistem çok katmanlı bir ISBN taraması çalıştırır:

1. **Akıllı Başlık Puanlama:** Arama sonuçlarındaki tüm kitaplar; kitap adı, anahtar kelimeler ve bağlantı üzerinden puanlanır; doğru kitap tam isabetle seçilir.
2. **Çok Aşamalı Temizleme:** Alt başlıklar, cilt numaraları ve parantez içi yayınevi ekleri temizlenerek ardışık sorgularla özel baskı kitaplar yakalanır.
3. **Kapsamlı ISBN Tespiti:** JSON-LD Schema, Ürün Özellikleri ve Sayfa İçi taramayla nadir ve eski baskılar dahil tüm ISBN numaraları eksiksiz ayıklanır.

---

## 🛡️ Mahremiyet ve Sıfır Log
1. **Veri Tabanı Yok:** Sistemde hiçbir veri tabanı bulunmaz.
2. **Erişim Günlüğü Yok:** IP adresleri ve kullanıcı adları kaydedilmez, loglar devre dışıdır.
3. **Parolasız ve Güvenli:** Yalnızca herkese açık kütüphane taranır; hesap şifresi kesinlikle talep edilmez.
4. **Anında Temizlik:** Üretilen CSV dosyası indirildiği anda (veya 15 dakika sonra) bellekten tamamen silinir.

---

## 📌 Diğer Meseleler

- **Alternatif Platformlar:** Bu araç yalnızca Goodreads ile sınırlı değildir; StoryGraph veya CSV içe aktarımını destekleyen herhangi bir kitap takip platformunda da rahatlıkla kullanılabilir.
- **Gizli Raflar:** Okuma geçmişinin çekilebilmesi için ilgili 1000Kitap profilindeki "Okuduklarım" rafının gizli olmaması gerekmektedir. Şayet rafınız gizliyse, aktarım yapmadan önce 1000Kitap Gizlilik ayarlarından geçici olarak herkese açık hâle getirmeniz gerekir.
- **Ekstra Mahremiyet Tavsiyesi:** Bu araç sadece kullanıcı adınıza ihtiyaç duyar ve bunu depolamaz. Yine de en üst düzey mahremiyet isteyen kullanıcıların, aktarım öncesi 1000Kitap üzerinden kullanıcı adlarını geçici olarak değiştirip aktarım sonrası eski adlarına dönmeleri tavsiye edilir.
