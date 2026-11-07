# SP Reference Data

Dados públicos de referência viária de São Paulo, normalizados para consulta incremental. O repositório reúne posições, atributos e um índice compacto de geometria, com versões imutáveis e verificação SHA-256.

## Arquivos

- `latest.json`: versão corrente, compatibilidade, contagem, tamanho e hashes.
- `data/`: arquivos de dados identificados pelo hash de seu conteúdo.
- `status.json`: última consulta concluída, fontes, contagens e pendências.
- `tools/`: importação, validação e publicação.

## Fontes e atribuição

- [CET-SP](https://www.cetsp.com.br/consultas/fiscalizacao-eletronica-do-transito.aspx)
- [DER-SP](https://dersp.der.sp.gov.br/Upload/XML/radares.xml)
- [ANTT — dados abertos](https://dados.antt.gov.br/dataset/radar)
- [SEMIL/ARTESP](https://mapas.semil.sp.gov.br/server/rest/services/Hosted/ARTESP_Pontos_rodovias/FeatureServer/0)
- Geometria derivada de [OpenStreetMap, sob ODbL](https://www.openstreetmap.org/copyright).

Os dados mantêm atribuição aos respectivos órgãos públicos. A cobertura pode ser parcial ou defasada. A ausência de um atributo representa informação não confirmada. Este repositório não recebe dados de uso, trajetos nem localização de pessoas.

## Atualização

Uma rotina diária às 08:17 UTC consulta as fontes e publica uma nova versão somente se houver mudanças. Usa Python 3.12, biblioteca padrão e um runner padrão gratuito do GitHub Actions em repositório público.

Respostas incompletas, identidades conflitantes e alterações anormais de contagem interrompem a publicação. Registros com coordenadas inválidas são rejeitados e registrados para revisão. Divergências do XML do DER não substituem automaticamente posições previamente revisadas. A ausência isolada no XML não equivale a uma desativação; exige-se registro explícito e compatível.

Associações com vias e sentidos só são preservadas quando continuam compatíveis. Itens novos podem permanecer sem essa associação até revisão. Limites não são deduzidos de pontos vizinhos ou da classificação geral de uma rodovia.

Para reproduzir: `python tools/updates/refresh.py .`.
Para testar: `python -m unittest discover -s tools/updates -p 'test_*.py'`.

Falhas ficam na aba Actions e preservam a última edição válida. Mudanças nas fontes podem exigir manutenção dos importadores. Tarefas agendadas podem sofrer atrasos ou ser desativadas pelo GitHub após 60 dias sem atividade; a auditoria de uma consulta concluída gera um commit diário. Confira a rotina após uma interrupção prolongada. As condições dos serviços gratuitos podem mudar.
