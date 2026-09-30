# SpotifyMix: documento di progetto

Data: 2026-09-30. Stato: da rileggere e approvare.

## 1. Obiettivo

Chi lavora con Claude Code ascolta musica per gran parte del tempo, ma il feed di Spotify ripropone sempre gli stessi brani. SpotifyMix capisce che attività si sta svolgendo in Claude Code (sviluppo, debug, pianificazione, ...) e fa suonare su Spotify musica nuova del genere adatto a quell'attività.

**Regola unica e non negoziabile:** non riprodurre mai un brano ascoltato o salvato negli ultimi 7 giorni.

Successo significa:
- durante una giornata di lavoro la musica cambia genere quando cambia davvero l'attività, senza intervento manuale;
- nessun brano sentito o salvato negli ultimi 7 giorni viene proposto;
- Claude Code non viene mai rallentato né bloccato, anche se SpotifyMix è spento o guasto;
- il repository è pubblico e installabile da chiunque, senza dati personali dell'autore.

### Cosa ha chiesto l'autore e cosa è stato dedotto

Richiesto esplicitamente: rilevamento da Claude Code con comando manuale di riserva; le modalità e i generi della sezione 5; nessun cambio mentre Claude aspetta l'utente; a fine sessione si continua con l'ultimo genere; servizio sempre acceso (opzione B); nessun intervento se la musica è in pausa o suona su un altro dispositivo; repository pubblico `caesla/spotifymix`; Windows, Mac e Linux; autore delle modifiche "caesla" con indirizzo anonimo GitHub.

Dedotto (da confermare in revisione): comportamento quando l'utente sceglie da sé una playlist (sezione 7.4); soglia di 3 minuti per il cambio di modalità; frequenze di controllo.

## 2. Vincoli esterni verificati (30/09/2026)

Fonti: documentazione ufficiale per sviluppatori Spotify e documentazione ufficiale Claude Code. Il post di partenza citava "Song DNA": è una funzione dell'app Spotify (dati WhoSampled, marzo 2026), non esiste un'interfaccia pubblica per programmi. SpotifyMix usa la normale Web API di Spotify.

**Spotify Web API**
- Le app nuove sono in "Development mode": massimo 5 utenti per app, il proprietario deve avere Premium. L'accesso esteso è riservato ad aziende con 250.000 utenti mensili. Conseguenza: **ogni utente crea la propria app Spotify**.
- Non disponibili per le app nuove: brani consigliati, artisti simili, caratteristiche audio, playlist editoriali. La scoperta di musica nuova va costruita con la ricerca.
- Ricerca: filtro `genre:` supportato per brani e artisti; massimo 10 risultati per richiesta (da febbraio 2026); spostamento massimo nei risultati 1000.
- Ascolti recenti: solo gli ultimi 50 brani. Uno storico di 7 giorni va accumulato da SpotifyMix.
- Brani salvati: disponibili con data di salvataggio (`added_at`).
- Controllo della riproduzione (avvio, coda): richiede Premium.
- L'etichetta "genere" degli artisti è segnalata come poco affidabile dal 2025 (solo segnalazioni della comunità): non la usiamo come unico criterio.

**Claude Code hook** (versione installata 2.1.285)
- Campi comuni a ogni segnale: `session_id`, `cwd`, `permission_mode` (valori tra cui `plan`), `hook_event_name`, `transcript_path`, più `agent_id`/`agent_type` dentro un subagent.
- `PreToolUse`: `tool_name`, `tool_input`, `tool_use_id`. Esistono `PostToolUseFailure`, `Notification`, `SubagentStart`, `SubagentStop`, `UserPromptSubmit`, `SessionEnd`.
- Opzione `"async": true`: l'aggancio gira in sottofondo e non blocca mai.
- I campi di dettaglio di alcuni eventi non sono documentati: **vanno registrati dal vivo prima di scrivere il classificatore** (sezione 11).

## 3. Architettura

```
Claude Code (qualsiasi progetto)
   │ hook async: `spotifymix hook` legge il segnale e lo inoltra (timeout 1 s, esce sempre con 0)
   ▼
Servizio SpotifyMix (processo sempre acceso, solo 127.0.0.1)
   ├── Classificatore: segnali → modalità stabile
   ├── Regista: decide quando e cosa accodare
   ├── Cercatore: trova brani nuovi per genere
   ├── Registratore: accumula ascolti e preferiti
   └── Client Spotify: login PKCE, rinnovo token, limiti di richieste
   ▼
Archivio SQLite (cartella utente)          Spotify Web API
```

### Componenti

| Unità | Cosa fa | Dipende da |
|---|---|---|
| `hook` (comando) | legge il segnale da stdin, lo invia al servizio con il token locale, esce 0 in ogni caso | solo libreria standard |
| `server` | riceve i segnali su `127.0.0.1`, porta configurabile; rifiuta richieste senza intestazione `X-SpotifyMix-Token` | classificatore |
| `classifier` | trasforma un segnale in un "voto" per una modalità secondo le regole; tiene la finestra degli ultimi 3 minuti; espone la modalità stabile | file regole |
| `director` | osserva il brano in corso e accoda il prossimo brano della modalità stabile poco prima della fine | player, finder, store |
| `finder` | produce brani candidati per un genere, già filtrati dalla regola dei 7 giorni | client Spotify, store |
| `recorder` | ogni 10 minuti salva gli ultimi ascolti; ogni 30 minuti aggiorna i preferiti | client Spotify, store |
| `spotify` | chiamate HTTP, login PKCE, rinnovo token, attesa su errore 429 | keyring |
| `store` | SQLite: ascolti, preferiti, brani proposti, stato | nessuna |
| `cli` | `setup`, `stato`, `modo <nome>`, `pausa <durata>`, `riprendi`, `service install/uninstall` | tutti |

Ogni unità si prova da sola con dati finti. Il classificatore non sa nulla di Spotify; il cercatore non sa nulla di Claude Code.

## 4. Scelte tecniche

- Python 3.12, gestione con uv, installazione come strumento: `uv tool install git+https://github.com/caesla/spotifymix`.
- Dipendenze previste: `httpx` (HTTP), `keyring` (cassaforte di sistema), `platformdirs` (cartelle utente). Server HTTP locale leggero: la scelta precisa è rimandata al piano, preferendo la libreria standard.
- Login Spotify con **Authorization Code + PKCE**: nessuna chiave segreta esiste.
- Permessi Spotify richiesti: `user-read-recently-played`, `user-library-read`, `user-read-playback-state`, `user-read-currently-playing`, `user-modify-playback-state`.
- Avvio automatico: Utilità di pianificazione (Windows), launchd agent utente (macOS), systemd unit utente (Linux), con riavvio in caso di arresto.

## 5. Modalità e generi predefiniti

I generi sono i valori predefiniti del repository; ogni utente li sovrascrive in un file nella propria cartella utente. Ogni modalità ha un genere di riserva, usato se il principale non dà risultati.

| # | Modalità | Generi | Segnale principale |
|---|---|---|---|
| 1 | Pianificazione | ambient | `permission_mode = plan`, skill di pianificazione |
| 2 | Brainstorming | post-rock, nu jazz | skill di brainstorming, strumento di domande a scelta multipla |
| 3 | Sviluppo | deep house, tech house | modifica di file di codice |
| 4 | Debug | hip hop, rap | strumento fallito, skill di debug |
| 5 | Test | techno | comandi di test |
| 6 | Revisione | lo-fi hip hop | skill di revisione, `git diff` |
| 7 | Esplorazione | downtempo, trip hop | lettura file, ricerche web, agent Explore |
| 8 | Scrittura | deep house, tech house | modifica di `.md`, `.docx`, documenti |
| 9 | Interfacce | synthwave, nu disco | modifica di file di stile e pagine, strumenti browser |
| 10 | Rilascio | funk, disco | `git commit/push`, `gh pr`, deploy |
| 11 | Orchestrazione | deep house, tech house | più subagent attivi |
| 12 | In attesa dell'utente | nessun cambio | `Notification` di permesso o inattività |
| 13 | Fine sessione | si continua con l'ultimo genere | `SessionEnd` |

Le etichette dei generi vanno verificate contro la ricerca reale di Spotify (sezione 11); quelle che non danno risultati vengono sostituite con la più vicina e segnalate all'autore.

## 6. Classificazione

1. Ogni segnale produce al massimo un voto, secondo le regole in ordine di priorità (vince la prima che si applica): manuale, debug, pianificazione, brainstorming, rilascio, test, revisione, orchestrazione, interfacce, scrittura, sviluppo, esplorazione.
2. Le parole del messaggio dell'utente (es. "errore", "non funziona") aggiungono un voto più leggero, mai decisivo da solo.
3. La **modalità stabile** è quella con più voti negli ultimi 3 minuti, sommando tutte le sessioni di Claude Code aperte. Cambia solo se la nuova vince per almeno 3 minuti consecutivi.
4. Un comando manuale (`spotifymix modo debug`) ha precedenza assoluta fino a scadenza (predefinita 1 ora) o fino a `spotifymix riprendi`.
5. Le regole stanno in un file dati leggibile, non sparse nel codice.

**Riservatezza dei segnali:** i segnali contengono testo dei messaggi e percorsi di file. Il servizio li usa solo in memoria; su disco salva unicamente modalità, orario e nome dell'evento. Mai il testo dei messaggi, mai i percorsi.

## 7. Riproduzione

### 7.1 Quando SpotifyMix agisce
Solo se la musica sta suonando su un dispositivo di tipo computer il cui nome corrisponde a questo PC (configurabile). In pausa, o su telefono, auto, altoparlanti: non fa nulla.

### 7.2 Come accoda
Il regista controlla il brano in corso (ogni 5 secondi solo verso la fine del brano, più di rado altrimenti). Circa 20 secondi prima della fine accoda **un solo brano** della modalità stabile. Accodando un brano alla volta, un cambio di modalità ha effetto dal brano successivo senza dover svuotare code (la Web API non permette di svuotarle).

### 7.3 Fine sessione e attesa
Il servizio non dipende dalle sessioni: finché la musica suona continua ad accodare brani dell'ultima modalità stabile. L'attesa dell'utente (modalità 12) non produce voti.

### 7.4 Se l'utente sceglie da sé (dedotto, da confermare)
Se l'utente fa partire da Spotify una propria playlist, un album o un brano, SpotifyMix se ne accorge (il brano in corso non è uno di quelli accodati da lui) e si fa da parte fino al successivo cambio di modalità stabile o a `spotifymix riprendi`.

### 7.5 Scelta dei brani
- Ricerca `genre:"<genere>"` su brani, con spostamento casuale nei risultati e intervalli di anni variabili, per evitare di pescare sempre i brani più famosi.
- Scarto: brani ascoltati negli ultimi 7 giorni, brani salvati negli ultimi 7 giorni, brani già proposti da SpotifyMix negli ultimi 7 giorni.
- Scorta interna di circa 10 candidati per modalità, ricaricata quando scende sotto 3.
- Il confronto avviene per ID Spotify del brano; in più per coppia "titolo normalizzato + artista principale", per non riproporre lo stesso brano in un'altra edizione.

### 7.6 Regola dei 7 giorni: copertura reale
Coperti: tutto ciò che è stato ascoltato mentre il servizio era acceso (controllo ogni 10 minuti, 50 brani sono circa 3 ore di margine) e tutti i brani salvati. Non coperti: ascolti su altri dispositivi a PC spento oltre gli ultimi 50 brani. Il limite va scritto nel README.

## 8. Gestione degli errori

| Situazione | Comportamento |
|---|---|
| Servizio spento o bloccato | l'aggancio fallisce in silenzio entro 1 s, Claude Code prosegue normalmente |
| Errore nell'aggancio | intercettato ovunque, uscita sempre 0 |
| Spotify risponde 429 (limite) | attende il tempo indicato da Spotify, riprova; la musica in corso non si tocca |
| Spotify non raggiungibile | riprova con attese crescenti; registra nel log |
| Token scaduto | rinnovo automatico; se fallisce, stato "login richiesto" in `spotifymix stato` e nel log |
| Genere senza risultati | genere di riserva, voce nel log |
| Nessun candidato dopo i filtri | allarga intervallo di anni e spostamenti; se ancora nulla, non accoda e registra |
| Arresto del servizio | riavvio dal gestore di sistema |

## 9. Repository pubblico e dati personali

- Nessun segreto esiste nel progetto: login PKCE senza chiave segreta; il Client ID è dell'app di ciascun utente e sta nella sua configurazione locale.
- Token nella cassaforte di sistema (Gestore credenziali, Portachiavi, Secret Service). Se su Linux la cassaforte manca, file con permessi di sola lettura per l'utente, con avviso.
- Configurazione, archivio, log e token locale del servizio in cartella utente (`platformdirs`), mai nella cartella del progetto.
- gitleaks come controllo prima di ogni commit e come GitHub Action a ogni push e pull request.
- `.gitignore` per file locali, ambienti virtuali e database.
- Test solo con dati inventati; i segnali reali registrati vengono ripuliti (percorsi, nomi, testo dei messaggi) prima di diventare fixture.
- Identità delle modifiche: `caesla` con indirizzo anonimo GitHub, impostata solo in questo repository.
- Licenza MIT. README e codice in inglese per un pubblico internazionale; questo documento resta in italiano.

## 10. Installazione per un nuovo utente

1. `uv tool install git+https://github.com/caesla/spotifymix`
2. `spotifymix setup`, che guida a: creare l'app su developer.spotify.com con il redirect locale indicato; incollare il Client ID; fare login nel browser; scegliere il nome del dispositivo; aggiungere l'aggancio alle impostazioni utente di Claude Code (mostra la modifica e chiede conferma; salva una copia di riserva); installare l'avvio automatico.
3. `spotifymix stato` per vedere modalità, dispositivo, ultimo brano accodato ed eventuali errori.
4. `spotifymix uninstall` rimuove aggancio e avvio automatico.

## 11. Verifiche da fare prima o durante l'implementazione

Sono premesse non ancora provate sul sistema reale:
1. **Segnali reali**: registrare un giorno di segnali Claude Code su questo PC e confermare i campi usati dal classificatore (in particolare `PostToolUseFailure`, `SubagentStart`, `Notification`, `SessionEnd`, nome della skill in `tool_input`).
2. **Shell dell'aggancio su Windows**: confermare che `spotifymix hook` venga lanciato correttamente dalla configurazione hook su Windows, Mac e Linux.
3. **Etichette di genere**: provare ogni genere della sezione 5 con la ricerca reale e misurare quanti brani distinti restituisce.
4. **Accodamento**: confermare su dispositivo reale che accodare 20 secondi prima della fine produca il passaggio atteso, e come si comporta la riproduzione automatica di Spotify.
5. **Riconoscimento del dispositivo**: confermare che nome e tipo del dispositivo Spotify desktop identifichino questo PC.
6. **Quote**: misurare le chiamate al minuto nel funzionamento normale rispetto ai limiti della Development mode.

## 12. Divisione del lavoro

| Dove | Cosa |
|---|---|
| Sessione locale | spec, repository, verifiche 1-5 della sezione 11, login, aggancio globale (con conferma dell'autore), avvio automatico, prova completa |
| Autore | riscatto del credito cloud entro l'8/10/2026, installazione dell'app GitHub di Claude sul repository, creazione dell'app Spotify |
| Sessione cloud | codice e test di tutte le unità con Spotify simulato, CI su tre sistemi, README |

Le sessioni cloud non hanno browser né accesso a questo PC: login e riproduzione reale si provano solo in locale.

## 13. Fuori perimetro (prima versione)

- Rilevamento da Codex, Hermes, AGY o dalla finestra attiva (Codex ha un meccanismo di hook simile: possibile estensione futura).
- Fonti esterne di "artisti simili" (ListenBrainz, Deezer): possibile miglioramento se la ricerca per genere risulta ripetitiva.
- Interfaccia grafica.
- Importazione dello storico completo tramite richiesta dati GDPR.

## 14. Prova completa (definizione di finito)

Sul PC dell'autore, con la lista di controllo:
1. login riuscito e `spotifymix stato` pulito;
2. passaggio da sviluppo a debug e ritorno: il genere cambia dal brano successivo, dopo la soglia;
3. nessun brano proposto compare negli ascolti o nei preferiti degli ultimi 7 giorni (controllo sull'archivio);
4. musica in pausa: nessuna azione per 10 minuti di lavoro;
5. musica su telefono: nessuna azione;
6. servizio fermato a mano: Claude Code non mostra ritardi né errori;
7. riavvio del PC: il servizio riparte da solo;
8. `git log` e contenuto del repository senza email, percorsi personali o chiavi (gitleaks pulito).
