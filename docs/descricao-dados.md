[[Voltar ao README]](../README.md) • [[Diagrama ER]](diagrama_er.md)

# Descrição dos dados do CNPJ

### Layout para os dados abertos do CNPJ

### EMPRESAS

| Campo                               | Descrição                                                                                                                                                                  |
| :---------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CNPJ BÁSICO**                     | NÚMERO BASE DE INSCRIÇÃO NO CNPJ (OITO PRIMEIROS DÍGITOS DO CNPJ).                                                                                                         |
| **RAZÃO SOCIAL / NOME EMPRESARIAL** | NOME EMPRESARIAL DA PESSOA JURÍDICA                                                                                                                                        |
| **NATUREZA JURÍDICA**               | CÓDIGO DA NATUREZA JURÍDICA                                                                                                                                                |
| **QUALIFICAÇÃO DO RESPONSÁVEL**     | QUALIFICAÇÃO DA PESSOA FÍSICA RESPONSÁVEL PELA EMPRESA                                                                                                                     |
| **CAPITAL SOCIAL DA EMPRESA**       | CAPITAL SOCIAL DA EMPRESA                                                                                                                                                  |
| **PORTE DA EMPRESA**                | CÓDIGO DO PORTE DA EMPRESA:<br>1 - NÃO INFORMADO<br>2 - MICRO EMPRESA<br>03 - EMPRESA DE PEQUENO PORTE<br>05 - DEMAIS                                                      |
| **ENTE FEDERATIVO RESPONSÁVEL**     | O ENTE FEDERATIVO RESPONSÁVEL É PREENCHIDO PARA OS CASOS DE ÓRGÃOS E ENTIDADES DO GRUPO DE NATUREZA JURÍDICA 1XXX. PARA AS DEMAIS NATUREZAS, ESTE ATRIBUTO FICA EM BRANCO. |

---

### ESTABELECIMENTOS

| Campo | Descrição |
| :--- | :--- |
| **CNPJ BÁSICO** | NÚMERO BASE DE INSCRIÇÃO NO CNPJ (OITO PRIMEIROS DÍGITOS DO CNPJ). |
| **CNPJ ORDEM** | NÚMERO DO ESTABELECIMENTO DE INSCRIÇÃO NO CNPJ (DO NONO ATÉ O DÉCIMO SEGUNDO DÍGITO DO CNPJ). |
| **CNPJ DV** | DÍGITO VERIFICADOR DO NÚMERO DE INSCRIÇÃO NO CNPJ (DOIS ÚLTIMOS DÍGITOS DO CNPJ). |
| **IDENTIFICADOR MATRIZ/FILIAL** | CÓDIGO DO IDENTIFICADOR MATRIZ/FILIAL:<br>1 - MATRIZ<br>2 - FILIAL |
| **NOME FANTASIA** | CORRESPONDE AO NOME FANTASIA |
| **SITUAÇÃO CADASTRAL** | CÓDIGO DA SITUAÇÃO CADASTRAL:<br>01 - NULA<br>2 - ATIVA<br>3 - SUSPENSA<br>4 - INAPTA<br>08 - BAIXADA |
| **DATA SITUAÇÃO CADASTRAL** | DATA DO EVENTO DA SITUAÇÃO CADASTRAL |
| **MOTIVO SITUAÇÃO CADASTRAL** | CÓDIGO DO MOTIVO DA SITUAÇÃO CADASTRAL |
| **NOME DA CIDADE NO EXTERIOR** | NOME DA CIDADE NO EXTERIOR |
| **PAÍS** | CÓDIGO DO PAÍS |
| **DATA DE INÍCIO ATIVIDADE** | DATA DE INÍCIO DA ATIVIDADE |
| **CNAE FISCAL PRINCIPAL** | CÓDIGO DA ATIVIDADE ECONÔMICA PRINCIPAL DO ESTABELECIMENTO |
| **CNAE FISCAL SECUNDÁRIA** | CÓDIGO DA(S) ATIVIDADE(S) ECONÔMICA(S) SECUNDÁRIA(S) DO ESTABELECIMENTO |
| **TIPO DE LOGRADOURO** | DESCRIÇÃO DO TIPO DE LOGRADOURO |
| **LOGRADOURO** | NOME DO LOGRADOURO ONDE SE LOCALIZA O ESTABELECIMENTO. |
| **NÚMERO** | NÚMERO ONDE SE LOCALIZA O ESTABELECIMENTO. QUANDO NÃO HOUVER PREENCHIMENTO DO NÚMERO HAVERÁ 'S/N'. |
| **COMPLEMENTO** | COMPLEMENTO PARA O ENDEREÇO DE LOCALIZAÇÃO DO ESTABELECIMENTO |
| **BAIRRO** | BAIRRO ONDE SE LOCALIZA O ESTABELECIMENTO. |
| **CEP** | CÓDIGO DE ENDEREÇAMENTO POSTAL REFERENTE AO LOGRADOURO NO QUAL O ESTABELECIMENTO ESTA LOCALIZADO |
| **UF** | SIGLA DA UNIDADE DA FEDERAÇÃO EM QUE SE ENCONTRA O ESTABELECIMENTO |
| **MUNICÍPIO** | CÓDIGO DO MUNICÍPIO DE JURISDIÇÃO ONDE SE ENCONTRA O ESTABELECIMENTO |
| **DDD 1** | CONTÉM O DDD 1 |
| **TELEFONE 1** | CONTÉM O NÚMERO DO TELEFONE 1 |
| **DDD 2** | CONTÉM O DDD 2 |
| **TELEFONE 2** | CONTÉM O NÚMERO DO TELEFONE 2 |
| **DDD DO FAX** | CONTÉM O DDD DO FAX |
| **FAX** | CONTÉM O NÚMERO DO FAX |
| **CORREIO ELETRÔNICO** | CONTÉM O E-MAIL DO CONTRIBUINTE |
| **SITUAÇÃO ESPECIAL** | SITUAÇÃO ESPECIAL DA EMPRESA |
| **DATA DA SITUAÇÃO ESPECIAL** | DATA EM QUE A EMPRESA ENTROU EM SITUAÇÃO ESPECIAL |

---

### DADOS DO SIMPLES

| Campo | Descrição |
| :--- | :--- |
| **CNPJ BÁSICO** | NÚMERO BASE DE INSCRIÇÃO NO CNPJ (OITO PRIMEIROS DÍGITOS DO CNPJ). |
| **OPÇÃO PELO SIMPLES** | INDICADOR DA EXISTÊNCIA DA OPÇÃO PELO SIMPLES.<br>S - SIM<br>N - NÃO<br>EM BRANCO - OUTROS |
| **DATA DE OPÇÃO PELO SIMPLES** | DATA DE OPÇÃO PELO SIMPLES |
| **DATA DE EXCLUSÃO DO SIMPLES** | DATA DE EXCLUSÃO DO SIMPLES |
| **OPÇÃO PELO MEI** | INDICADOR DA EXISTÊNCIA DA OPÇÃO PELO MEI<br>S - SIM<br>N - NÃO<br>EM BRANCO - OUTROS |
| **DATA DE OPÇÃO PELO MEI** | DATA DE OPÇÃO PELO MEI |
| **DATA DE EXCLUSÃO DO MEI** | DATA DE EXCLUSÃO DO MEI |

---

### SÓCIOS

| Campo | Descrição |
| :--- | :--- |
| **CNPJ BÁSICO** | NÚMERO BASE DE INSCRIÇÃO NO CNPJ (CADASTRO NACIONAL DA PESSOA JURÍDICA). |
| **IDENTIFICADOR DE SÓCIO** | CÓDIGO DO IDENTIFICADOR DE SÓCIO<br>1 - PESSOA JURÍDICA<br>2 - PESSOA FÍSICA<br>3 - ESTRANGEIRO |
| **NOME DO SÓCIO (NO CASO PF) OU RAZÃO SOCIAL (NO CASO PJ)** | NOME DO SÓCIO PESSOA FÍSICA OU A RAZÃO SOCIAL E/OU NOME EMPRESARIAL DA PESSOA JURÍDICA E/OU NOME DO SÓCIO/RAZÃO SOCIAL DO SÓCIO ESTRANGEIRO |
| **CNPJ/CPF DO SÓCIO** | CPF OU CNPJ DO SÓCIO (SÓCIO ESTRANGEIRO NÃO TEM ESTA INFORMAÇÃO). |
| **QUALIFICAÇÃO DO SÓCIO** | CÓDIGO DA QUALIFICAÇÃO DO SÓCIO |
| **DATA DE ENTRADA SOCIEDADE** | DATA DE ENTRADA NA SOCIEDADE |
| **PAÍS** | CÓDIGO PAÍS DO SÓCIO ESTRANGEIRO |
| **REPRESENTANTE LEGAL** | NÚMERO DO CPF DO REPRESENTANTE LEGAL |
| **NOME DO REPRESENTANTE** | NOME DO REPRESENTANTE LEGAL |
| **QUALIFICAÇÃO DO REPRESENTANTE LEGAL** | CÓDIGO DA QUALIFICAÇÃO DO REPRESENTANTE LEGAL |
| **FAIXA ETÁRIA** | CÓDIGO CORRESPONDENTE À FAIXA ETÁRIA DO SÓCIO |

---
### **PAÍSES**

| Campo | Descrição |
| :--- | :--- |
| **CÓDIGO** | CÓDIGO DO PAÍS |
| **DESCRIÇÃO** | NOME DO PAÍS |

### **MUNICÍPIOS**

| Campo | Descrição |
| :--- | :--- |
| **CÓDIGO** | CÓDIGO DO MUNICÍPIO |
| **DESCRIÇÃO** | NOME DO MUNICÍPIO |

### **QUALIFICAÇÕES DE SÓCIOS**

| Campo | Descrição |
| :--- | :--- |
| **CÓDIGO** | CÓDIGO DA QUALIFICAÇÃO DO SÓCIO |
| **DESCRIÇÃO** | NOME DA QUALIFICAÇÃO DO SÓCIO |

### **NATUREZAS JURÍDICAS**

| Campo | Descrição |
| :--- | :--- |
| **CÓDIGO** | CÓDIGO DA NATUREZA JURÍDICA |
| **DESCRIÇÃO** | NOME DA NATUREZA JURÍDICA |

### **CNAES**

| Campo | Descrição |
| :--- | :--- |
| **CÓDIGO** | CÓDIGO DA ATIVIDADE ECONÔMICA |
| **DESCRIÇÃO** | NOME DA ATIVIDADE ECONÔMICA |

---

### **1. Formato do arquivo**
O formato do arquivo deve ter o padrão de carga automática em Bancos de Dados Relacionais (RDBMS – *Relational Database Management Systems*); usar ponto e vírgula (;) como separador de atributos;

### **2. O campo 169 (CNPJ/CPF DO SÓCIO) e 271 (CNPJ/CPF DO REPRESENTANTE) do layout de sócios devem ser descaracterizados conforme a regra abaixo:**
Ocultação de informações pessoais sigilosas como no caso do CPF, o qual deve ser descaracterizado por meio da ocultação dos três primeiros dígitos e dos dois dígitos verificadores, conforme orientação disposta no art. 129 § 2o da Lei no 13.473/2017 (LDO 2018).

### **3. Campo Ente Federativo Responsável - EFR, no Layout Principal (Dados Cadastrais):**
Deve ser preenchido para os casos de Órgãos e Entidades do grupo de Natureza Jurídica 1XX. Para as demais naturezas, esse atributo fica em branco.
Exemplos de texto que deverão aparecer no arquivo final: UNIÃO; DISTRITO FEDERAL; BAHIA; para municípios, exibir também a sigla da UF: SÃO PAULO – SP; BELO HORIZONTE - MG;

### **4. Campo Faixa Etária, no Layout Sócios:**
Baseada na data de nascimento do CPF de cada sócio, deverá ser criado o valor para o campo "Faixa Etária" conforme a regra abaixo:
- 1 para os intervalos entre 0 a 12 anos;
- 2 para os intervalos entre 13 a 20 anos;
- 3 para os intervalos entre 21 a 30 anos;
- 4 para os intervalos entre 31 a 40 anos;
- 5 para os intervalos entre 41 a 50 anos;
- 6 para os intervalos entre 51 a 60 anos;
- 7 para os intervalos entre 61 a 70 anos;
- 8 para os intervalos entre 71 a 80 anos;
- 9 para maiores de 80 anos.
- 0 para não se aplica.

### **5. O Campo CNAE FISCAL SECUNDÁRIA, no Layout Estabelecimentos:**
Deve ser preenchido com cada ocorrência sendo separada por vírgula, para os casos de várias ocorrências.

> **Nota sobre o que o pipeline guarda.** A Receita entrega esse campo separado por
> vírgula e entre aspas. O `src/schema.sql` o mantém como `VARCHAR`, com o literal
> convertido para o formato de brace `{1234567,9876543}`. Ver
> [diagrama_er.md](diagrama_er.md) para o impacto em consultas.
