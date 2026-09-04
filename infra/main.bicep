// Mock HR on Azure Container Apps (Consumption, cheapest).
//
// ONE Container App, ONE always-on replica. All containers share an EmptyDir
// volume mounted at /data holding the SQLite DB:
//   init container 'seed' -> bootstraps /data/hr.db if empty, before app start
//   'api'  container       -> FastAPI web console + REST on :8000
//   'mcp'  container       -> MCP streamable-HTTP on :8001 (/mcp)
//   'proxy' container      -> Caddy, the single external ingress on :8080
//
// DATA DURABILITY: EmptyDir lives and dies with the replica, so anything
// written through the REST/MCP surfaces survives only as long as the replica
// does. Two consequences:
//   * minReplicas must stay >= 1 (see the param) or an idle period silently
//     wipes everything;
//   * a new revision starts a new replica, so redeploying resets the dataset
//     back to the seeded snapshot.
// The seed itself is a bootstrap, not a reset: it leaves a populated DB alone
// unless run with --force / HR_SEED_FORCE=1, so restarts do not destroy data.
// For durability across redeploys, replace this volume with an Azure Files
// share -- note SQLite's WAL journal does not work over SMB, so hr_core.db
// would also need journal_mode switched away from WAL.
//
// Sizing: each container 0.25 vCPU / 0.5 GiB (ACA minimum). The 3 app
// containers sum to 0.75 vCPU / 1.5 GiB per replica, billed continuously
// because the app no longer scales to zero.

@description('Deployment region.')
param location string = resourceGroup().location

@description('Container App name (also used as the ingress subdomain prefix).')
param appName string = 'mock-hr'

@description('App image (init + api + mcp all use this).')
param appImage string = 'ghcr.io/changju-ahn/mock-hr-app:latest'

@description('Caddy proxy image.')
param proxyImage string = 'ghcr.io/changju-ahn/mock-hr-proxy:latest'

@description('Revision suffix. Defaults to a deploy-time timestamp so each redeploy rolls a fresh revision that re-pulls the (mutable :latest) images.')
param revisionSuffix string = 'r${utcNow('yyMMddHHmmss')}'

@description('Demo API key required in the X-API-Key header on all REST /api/* and MCP /mcp calls. Web console + /api/docs stay open.')
param apiKey string = 'changjuahn'

@description('Minimum replicas. Keep at 1: the SQLite DB sits on an ephemeral EmptyDir volume, so scaling to zero destroys every row written since the replica started. Set 0 only if resetting to the seeded snapshot on every idle period is acceptable.')
@minValue(0)
@maxValue(1)
param minReplicas int = 1

var dbPath = '/data/hr.db'
var dbEnv = [
  {
    name: 'HR_DB_PATH'
    value: dbPath
  }
]
// api + mcp additionally get the demo API key; the seed init container does not need it.
var appEnv = concat(dbEnv, [
  {
    name: 'HR_API_KEY'
    value: apiKey
  }
])
var containerResources = {
  cpu: json('0.25')
  memory: '0.5Gi'
}
var volumeMounts = [
  {
    volumeName: 'hrdata'
    mountPath: '/data'
  }
]

resource law 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${appName}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${appName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      revisionSuffix: revisionSuffix
      volumes: [
        {
          name: 'hrdata'
          storageType: 'EmptyDir'
        }
      ]
      initContainers: [
        {
          name: 'seed'
          image: appImage
          command: [
            'python'
            '-m'
            'hr_core.seed'
          ]
          resources: containerResources
          env: dbEnv
          volumeMounts: volumeMounts
        }
      ]
      containers: [
        {
          name: 'api'
          image: appImage
          command: [
            'uvicorn'
            'api.main:app'
            '--host'
            '0.0.0.0'
            '--port'
            '8000'
          ]
          resources: containerResources
          env: appEnv
          volumeMounts: volumeMounts
        }
        {
          name: 'mcp'
          image: appImage
          command: [
            'python'
            '-m'
            'mcp_server'
          ]
          resources: containerResources
          env: appEnv
          volumeMounts: volumeMounts
        }
        {
          name: 'proxy'
          image: proxyImage
          resources: containerResources
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: 1
      }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}/'
output restUrl string = 'https://${app.properties.configuration.ingress.fqdn}/api'
output restDocsUrl string = 'https://${app.properties.configuration.ingress.fqdn}/api/docs'
output mcpUrl string = 'https://${app.properties.configuration.ingress.fqdn}/mcp'
