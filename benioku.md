# 📚 1000Kitap'tan Goodreads'e Okuma Geçmişi Aktarıcı

1k-to-goodreads; 1000Kitap üzerindeki okuma geçmişinizi, verdiğiniz puanları ve okuma tarihlerinizi resmî **Goodreads CSV formatına** dönüştüren; veri tabanı barındırmayan, kullanıcı kaydı ve log tutmayan bir araçtır.

---


## 🛡️ Mahremiyet
1. **Veri Tabanı Yok:** Hiçbir SQL, SQLite, Redis vb. veri tabanı kurulmaz.
2. **Log Yok:** Uvicorn ve FastAPI erişim günlüklerinde IP adresleri veya kullanıcı adları tutulmaz.
3. **Şifresiz Güvenlik:** Sadece halka açık kütüphane taranır, hesap şifresi asla istenmez.
4. **Anında Bellek Temizliği:** Üretilen CSV dosyası indirildiği anda RAM'den derhal silinir ve Python Garbage Collector ile bellek serbest bırakılır.

---

## ⚙️ Teknik Mimari
- **Tasarım:** HTML5, CSS3 ve Modern Vanilla JavaScript (ES6+).
- **Backend:** Python 3.10+ ve FastAPI.
- **Hız Sınırlamalı FIFO Kuyruk:** 1000Kitap saniyede 2'den fazla istek geldiğinde IP engeli uygulayabildiğinden, `asyncio.Queue` ile istekler sıraya dizilir ve saniyede maksimum 2 istek (`sleep(0.55)`) atılarak güvenli tarama yapılır.
- **Canlı Akış:** Server-Sent Events (SSE) ile canlı sıra bilgisi, kitap tarama yüzdesi ve taranan son kitabın kapak önizlemesi. Olası ağ kopmalarında otomatik JSON Polling fallback desteği.
- **CSV Injection Koruması:** Spreadsheet yazılımlarının formül çalıştırmasını önlemek için `=`, `+`, `-`, `@` ile başlayan metinler güvenli hâle getirilir.
- **Karakter Seti:** Türkçe karakter bozulmalarını önlemek için UTF-8 BOM (`\ufeff`) ile kodlanır.


---

## Diğer Meseleler

- Teknik olarak bu araç sadece Goodreads'e aktarmakla sınırlı değildir. Okunulan tüm kitapları sadece tablo formatında görmek isteyenler de bu aracı kullanabilir. API desteği olan veya aktarmaya olanak tanıyan sistemi olan herhangi bir Goodreads türevi uygulamaya da aktarma gerçekleştirilebilir. Şayet satırlar uyumlu değilse, istediğiniz platforma uyumlu hâle getirmesi için bilginiz olmasa dahi büyük dil modellerinden herhangi birisine (DeepSeek, Gemini, Claude vb.) ilgili kodları atmanız yeterli olur.

- Aracı kullanabilmek için, okuma listesi alınacak olan hesabın okuduklarının gizli olmaması gerekir. Şayet gizliyse araç çalışmaz. Dolayısıyla, eğer okuduğunuz kitapların hepsini toplamak istiyorsanız, kütüphanenizi -**en azından geçici olarak**- herkese görünür kılmanız gerekli.

- **Mahremiyete düşkün olanlar için**: Bu araç sadece kullanıcı adınıza ihtiyaç duymaktadır ve bu bilgiyi de herhangi bir sunucuda depolamamaktadır. **Ancak**, verilerinizi çekebilmek için **zorunlu olarak** kullanıcı adınızın sunucudan geçmesi gerekmektedir. Sunucuda bu logları tutmasak bile mahremiyetiniz adına, bu aracı kullanmadan önce kişisel bilgilerinizi **1000Kitap** üzerinden gizlemeniz ve kullanıcı adınızı da değiştirerek değiştirdiğiniz kullanıcı adı üzerinden işlemlerinizi yapmanız tavsiye edilir -mahremiyet açısından sistem herhangi bir sorun içermese de-.
