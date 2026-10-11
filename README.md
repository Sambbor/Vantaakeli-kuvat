# Vantaakeli-kuvat – kelikameroiden kuvapankki

Vantaan maanteiden hoitourakan kelikameroiden kuvat ja samanhetkiset tiesääasemien mittaukset.
Aineistolla opetetaan kameroille tienpinnan tilan tunnistus (kuiva, märkä, kuura, lumi, jää, sohjo),
jotta kamerat voivat toimia "virtuaalisina tiesääasemina" myös siellä, missä asemaa ei ole.
Kelisivu: https://sambbor.github.io/Vantaakeli/

## Miten kerätään
- `kuvapankki.py` ajetaan kerran tunnissa (`.github/workflows/kuvapankki.yml`).
- **Parit:** kamerat, jotka ovat enintään 300 m päässä tiesääasemasta. Kuva joka tunti; aseman mittaus samalta hetkeltä on kuvan merkintä.
- **Vertailu:** muut urakan kamerat 3 tunnin välein, kun talvikeli on mahdollinen (kylmin asema ≤ +3 °C tai jollain asemalla muu kuin kuiva keli).
- Kuivan kelin kuvista tallennetaan joka 6., märän joka 2., talviset kuvat ja kelin vaihtumiset aina.
- Kuvat 400 px leveinä (`img/YYYY-MM/`), merkinnät `data/labels-YYYY-MM.csv`, yhteenveto `data/index.json`.

## Merkinnät (labels-YYYY-MM.csv)
`keli` (0 vika, 1 kuiva, 2 kostea, 3 märkä, 4 märkä ja suolattu, 5 kuura, 6 lumi, 7 jää, 8 tod. kostea ja suolainen, 9 sohjoinen),
`warn` (0 OK, 1 Varo, 2 Häly, 3 Kuura, 4 Sade), tienpinta `tr`, ilma `ta`, kastepiste `td`, kuurapiste `fp`, jäätymispiste `tf`,
vesi/lumi/jää `water/snow/ice` (mm), kitka `fric`, suola `salt`, sade `rain/rain_i/rain_form`, näkyvyys `vis`, tuuli `ws`,
auringon korkeus `sun`, kuvan kirkkaus `mean_L`. `label_ok = 1`, kun aseman mittaus on enintään 25 min kuvan ajasta.

Lähde: Fintraffic / digitraffic.fi, lisenssi CC BY 4.0.
