<template>
  <q-dialog v-model="dialogModel">
    <q-card class="upload-info-card">
      <div class="q-ma-md full-height">
        <div class="row full-height">
          <div class="row-xs col-md-8 q-my-auto q-pr-md">
            <q-img
              :src="resourceLink"
              style="min-height: 150px; min-width: 150px;"
            >
              <template v-slot:error>
                <div
                  class="absolute-full flex flex-center bg-negative text-white"
                >
                  Cannot load image
                </div>
              </template>
            </q-img>
          </div>
          <div class="row-xs col-md-4">
            <div class="row" id="upload-info-box">
              <q-input
                readonly
                label="URL"
                outlined
                v-model="fullLink"
                class="full-width"
              />
            </div>
            <q-card-actions align="center" vertical>
              <UploadActionButton
                label="Copy Relative URL"
                @click="copyRelativeURL"
              />
              <UploadActionButton
                label="Copy Absolute URL"
                @click="copyAbsoluteLink"
              />
              <UploadActionButton
                label="Delete PERMANENTLY"
                color="negative"
                @click="showDeleteDialog"
              />
            </q-card-actions>
          </div>
        </div>
      </div>
    </q-card>
  </q-dialog>
</template>

<script>
  import { BASE_URL } from '@/services/api_entry';
  import { copyToClipboard } from 'quasar';
  import deleteMixin from '@/components/ControlPanel/mixins/DeleteMixin';

  export default {
    mixins: [deleteMixin],
    components: {
      UploadActionButton: () =>
        import('@/components/ControlPanel/parts/buttons/UploadActionButton'),
    },
    model: {
      prop: 'showDialog',
      event: 'changeDialogStatus',
    },
    props: {
      resourceLink: {
        type: String,
      },
      showDialog: {
        type: Boolean,
        default: false,
      },
    },
    computed: {
      fullLink() {
        return BASE_URL + this.resourceLink;
      },
      dialogModel: {
        set(d) {
          this.$emit('changeDialogStatus', d);
        },
        get() {
          return this.showDialog;
        },
      },
    },
    methods: {
      copyRelativeURL() {
        copyToClipboard(this.resourceLink);
      },
      copyAbsoluteLink() {
        copyToClipboard(this.fullLink);
      },
    },
  };
</script>

<style lang="sass">
  .upload-info-card
    min-width: 50vw
    max-width: 50vw
    max-height: 50vh
</style>
