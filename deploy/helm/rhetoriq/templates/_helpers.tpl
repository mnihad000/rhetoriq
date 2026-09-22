{{- define "rhetoriq.name" -}}rhetoriq{{- end }}
{{- define "rhetoriq.labels" -}}
app.kubernetes.io/name: rhetoriq
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
{{- define "rhetoriq.image" -}}{{ .repository }}@{{ .digest }}{{- end }}
{{- define "rhetoriq.appEnv" -}}
- name: DEPLOYMENT_ENV
  value: production
- name: DATABASE_MIGRATION_MODE
  value: verify
- name: DATABASE_URL
  valueFrom: {secretKeyRef: {name: {{ .Values.global.secretName }}, key: database-url}}
- name: KAFKA_BOOTSTRAP_SERVERS
  value: kafka:19092
- name: KAFKA_SCHEMA_REGISTRY_URL
  value: http://apicurio:8080/apis/ccompat/v7
- name: ENABLE_B5_RETRIEVAL
  value: "true"
- name: ENABLE_POSTGRES_VECTOR_SEARCH
  value: "true"
- name: EMBEDDING_LOCAL_ONLY
  value: "true"
- name: ELASTICSEARCH_URL
  value: https://elasticsearch:9200
- name: ELASTICSEARCH_USERNAME
  value: elastic
- name: ELASTICSEARCH_PASSWORD
  valueFrom: {secretKeyRef: {name: {{ .Values.global.secretName }}, key: elasticsearch-password}}
- name: ELASTICSEARCH_CA_CERT
  value: /etc/rhetoriq/ca/ca.crt
- name: NEO4J_URL
  value: bolt://neo4j:7687
- name: NEO4J_USERNAME
  value: neo4j
- name: NEO4J_PASSWORD
  valueFrom: {secretKeyRef: {name: {{ .Values.global.secretName }}, key: neo4j-password}}
- name: NEO4J_CA_CERT
  value: /etc/rhetoriq/ca/ca.crt
- name: REDIS_URL
  value: rediss://redis:6379/0
- name: REDIS_PASSWORD
  valueFrom: {secretKeyRef: {name: {{ .Values.global.secretName }}, key: redis-password}}
- name: REDIS_CA_CERT
  value: /etc/rhetoriq/ca/ca.crt
- name: SEARXNG_BASE_URL
  value: http://searxng:8080
- name: FLINK_REST_URL
  value: http://flink-jobmanager:8081
- name: ENABLE_FLINK_TRENDING
  value: "true"
- name: BROWSER_RENDERING_ENABLED
  value: "false"
- name: B5_RECORDED_PROVIDER_FIXTURE
  value: /opt/rhetoriq/recorded-provider.json
{{- end }}
{{- define "rhetoriq.appVolumes" -}}
- name: internal-ca
  configMap: {name: rhetoriq-internal-ca}
- name: app-tmp
  emptyDir: {sizeLimit: 128Mi}
{{- end }}
{{- define "rhetoriq.appVolumeMounts" -}}
- {name: internal-ca, mountPath: /etc/rhetoriq/ca, readOnly: true}
- {name: app-tmp, mountPath: /tmp}
{{- end }}
{{- define "rhetoriq.appSecurity" -}}
allowPrivilegeEscalation: false
capabilities: {drop: ["ALL"]}
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
seccompProfile: {type: RuntimeDefault}
{{- end }}
